from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Dict, List, Tuple
import zlib


class GitObject:
    def __init__(self, objType: str, content: bytes):
        self.type = objType
        self.content = content

    def hash(self) -> str:
        # f(<type> <size>\0<content>)
        header = f"{self.type} {len(self.content)}\0".encode()
        return hashlib.sha1(header + self.content).hexdigest()

    def serialize(self) -> bytes:
        header = f"{self.type} {len(self.content)}\0".encode()
        return zlib.compress(header + self.content)

    @classmethod
    def deserialize(cls, data: bytes) -> GitObject:
        decompressed = zlib.decompress(data)
        nullIdx = decompressed.find(b"\0")
        header = decompressed[:nullIdx].decode()
        content = decompressed[nullIdx + 1 :]

        objType, _ = header.split(" ")

        return cls(objType, content)


class Blob(GitObject):
    def __init__(self, content: bytes):
        super().__init__("blob", content)


class Tree(GitObject):
    def __init__(self, entries: List[Tuple[str, str, str]] = None):
        self.entries = entries or []
        content = self._serialize_entries()
        super().__init__("tree", content)

    def _serialize_entries(self) -> bytes:
        # 100644 <name>\0<hash><mode> <name>\0<hash><mode> <name>\0<hash><mode> <name>\0<hash>
        content = b""
        for mode, name, objHash in sorted(self.entries):
            content += f"{mode} {name}\0".encode()
            content += bytes.fromhex(objHash)

        return content

    def addEntry(self, mode: str, name: str, objHash: str):
        self.entries.append((mode, name, objHash))
        self.content = self._serialize_entries()

    @classmethod
    def fromContent(cls, content: bytes) -> Tree:
        tree = cls()
        i = 0

        while i < len(content):
            nullIdx = content.find(b"\0", i)
            if nullIdx == -1:
                break

            modeName = content[i:nullIdx].decode()
            mode, name = modeName.split(" ", 1)
            objHash = content[nullIdx + 1 : nullIdx + 21].hex()
            tree.entries.append((mode, name, objHash))

            i = nullIdx + 21

        return tree


class Commit(GitObject):
    def __init__(self, treeHash: str, parentHashes: List[str], author: str, committer: str, message: str, timestamp: int = None):
        self.treeHash = treeHash
        self.parentHashes = parentHashes
        self.author = author
        self.committer = committer
        self.message = message
        self.timestamp = timestamp or int(time.time())

        content = self._serialize_commit()
        super().__init__("commit", content)

    def _serialize_commit(self):
        lines = [f"tree {self.treeHash}"]
        for parent in self.parentHashes:
            lines.append(f"parent {parent}")

        lines.append(f"author {self.author} {self.timestamp} +0000")
        lines.append(f"committer {self.committer} {self.timestamp} +0000")
        lines.append("")
        lines.append(self.message)

        return "\n".join(lines).encode()

    @classmethod
    def fromContent(cls, content: bytes) -> Commit:
        lines = content.decode().split("\n")
        treeHash = None
        parentHashes = []
        author = None
        committer = None
        messageStart = 0

        for i, line in enumerate(lines):
            if line.startswith("tree "):
                treeHash = line[5:]
            elif line.startswith("parent "):
                parentHashes.append(line[7:])
            elif line.startswith("author "):
                authorParts = line[7:].rsplit(" ", 2)
                author = authorParts[0]
                timestamp = int(authorParts[1])
            elif line.startswith("committer "):
                committerParts = line[10:].rsplit(" ", 2)
                committer = committerParts[0]
            elif line == "":
                messageStart = i + 1
                break

        message = "\n".join(lines[messageStart:])
        commit = cls(treeHash, parentHashes, author, committer, message, timestamp)
        return commit


class Repository:
    def __init__(self, path="."):
        self.path = Path(path).resolve()
        self.gitDir = self.path / ".pygit"

        # .git/objects
        self.objectsDir = self.gitDir / "objects"

        # .git/refs
        self.refDir = self.gitDir / "refs"
        self.headsDir = self.refDir / "heads"

        # HEAD file
        self.headFile = self.gitDir / "HEAD"

        # .git/index
        self.indexFile = self.gitDir / "index"

    def init(self) -> bool:
        if self.gitDir.exists():
            return False

        # create directories
        self.gitDir.mkdir()
        self.objectsDir.mkdir()
        self.refDir.mkdir()
        self.headsDir.mkdir()

        # create initial HEAD pointing to a branch
        self.headFile.write_text("ref: refs/heads/master\n")

        self.saveIndex({})

        print(f"Initialized empty Git repository in {self.gitDir}")

        return True

    def storeObject(self, obj: GitObject) -> str:
        objHash = obj.hash()
        objDir = self.objectsDir / objHash[:2]
        objFile = objDir / objHash[2:]

        if not objFile.exists():
            objDir.mkdir(exist_ok=True)
            objFile.write_bytes(obj.serialize())

        return objHash

    def loadIndex(self) -> Dict[str, str]:
        if not self.indexFile.exists():
            return {}

        try:
            return json.loads(self.indexFile.read_text())
        except:
            return {}

    def saveIndex(self, index: Dict[str, str]):
        self.indexFile.write_text(json.dumps(index, indent=2))

    def addFile(self, path: str):
        fullPath = self.path / path
        if not fullPath.exists():
            raise FileNotFoundError(f"Path {path} not found")
        # Read the file content
        content = fullPath.read_bytes()
        # Create BLOB object from the content
        blob = Blob(content)
        # store the blob object in database (.git/objects)
        blobHash = self.storeObject(blob)
        # Update index to include the file
        index = self.loadIndex()
        index[path] = blobHash
        self.saveIndex(index)

        print(f"Added {path}")

    def addDirectory(self, path: str):
        fullPath = self.path / path
        if not fullPath.exists():
            raise FileNotFoundError(f"Directory {path} not found")
        if not fullPath.is_dir():
            raise ValueError(f"{path} is not a directory")
        index = self.loadIndex()
        addedCount = 0
        # recursively traverse the directory
        for filePath in fullPath.rglob("*"):
            if filePath.is_file():
                if ".pygit" in filePath.parts or ".git" in filePath.parts:
                    continue

                # create & store blob object
                content = filePath.read_bytes()
                blob = Blob(content)
                blobHash = self.storeObject(blob)
                # update index
                relPath = str(filePath.relative_to(self.path))
                index[relPath] = blobHash
                addedCount += 1

        self.saveIndex(index)

        if addedCount > 0:
            print(f"Added {addedCount} files from directory {path}")
        else:
            print(f"Directory {path} already up to date")

    def addPath(self, path: str) -> None:
        fullPath = self.path / path

        if not fullPath.exists():
            raise FileNotFoundError(f"Path {path} not found")

        if fullPath.is_file():
            self.addFile(path)
        elif fullPath.is_dir():
            self.addDirectory(path)
        else:
            raise ValueError(f"{path} is neither a file nor a directory")

    def loadObject(self, objHash: str) -> GitObject:
        objDir = self.objectsDir / objHash[:2]
        objFile = objDir / objHash[2:]

        if not objFile.exists():
            raise FileNotFoundError(f"Object {objHash} not found")

        return GitObject.deserialize(objFile.read_bytes())

    def createTreeFromIndex(self):
        index = self.loadIndex()
        if not index:
            tree = Tree()
            return self.storeObject(tree)

        dirs = {}
        files = {}

        for filePath, blobHash in index.items():
            parts = filePath.split("/")

            if len(parts) == 1:
                # file in root
                files[parts[0]] = blobHash
            else:
                dirName = parts[0]
                if dirName not in dirs:
                    dirs[dirName] = {}

                current = dirs[dirName]
                for part in parts[1:-1]:
                    if part not in current:
                        current[part] = {}

                    current = current[part]

                current[parts[-1]] = blobHash

        def createTreeRecursive(entriesDict: Dict):
            tree = Tree()

            for name, blobHash in entriesDict.items():
                if isinstance(blobHash, str):
                    tree.addEntry("100644", name, blobHash)

                if isinstance(blobHash, dict):
                    subtreeHash = createTreeRecursive(blobHash)
                    tree.addEntry("40000", name, subtreeHash)

            return self.storeObject(tree)

        rootEntries = {**files}
        for dirName, dirContents in dirs.items():
            rootEntries[dirName] = dirContents

        return createTreeRecursive(rootEntries)

    def getCurrentBranch(self) -> str:
        if not self.headFile.exists():
            return "master"

        headContent = self.headFile.read_text().strip()
        if headContent.startswith("ref: refs/heads/"):
            return headContent[16:]

        return "HEAD"  # detached HEAD

    def getBranchCommit(self, currentBranch: str):
        branchFile = self.headsDir / currentBranch

        if branchFile.exists():
            return branchFile.read_text().strip()

        return None

    def set_branch_commit(self, currentBranch: str, commitHash: str):
        branchFile = self.headsDir / currentBranch
        branchFile.write_text(commitHash + "\n")

    def commit(
        self,
        message: str,
        author: str = "PyGit User <user@pygit.com>",
    ):
        # create a tree object from the index (staging area)
        treeHash = self.createTreeFromIndex()

        currentBranch = self.getCurrentBranch()
        parentCommit = self.getBranchCommit(currentBranch)
        parentHashes = [parentCommit] if parentCommit else []

        index = self.loadIndex()
        if not index:
            print("nothing to commit, working tree clean")
            return None

        if parentCommit:
            parentGitCommitObj = self.loadObject(parentCommit)
            parentCommitData = Commit.fromContent(parentGitCommitObj.content)
            if treeHash == parentCommitData.treeHash:
                print("nothing to commit, working tree clean")
                return None

        commit = Commit(
            treeHash=treeHash,
            parentHashes=parentHashes,
            author=author,
            committer=author,
            message=message,
        )
        commitHash = self.storeObject(commit)

        self.set_branch_commit(currentBranch, commitHash)
        self.saveIndex({})
        print(f"Created commit {commitHash} on branch {currentBranch}")
        return commitHash

    def getFilesFromTreeRecursive(
        self,
        treeHash: str,
        prefix: str = "",
    ):
        files = set()
        try:
            treeObj = self.loadObject(treeHash)
            tree = Tree.fromContent(treeObj.content)
            # list<tuple<str, str, str>>
            for mode, name, objHash in tree.entries:
                fullName = f"{prefix}{name}"
                if mode.startswith("100"):
                    files.add(fullName)
                elif mode.startswith("400"):
                    subtreeFiles = self.getFilesFromTreeRecursive(
                        objHash, f"{fullName}/"
                    )
                    files.update(subtreeFiles)
        except Exception as e:
            print(f"Warning: Could not read tree {treeHash}: {e}")

        return files

    def checkout(self, branch: str, createBranch: bool):
        # computed the files to clear from the previous branch
        previousBranch = self.getCurrentBranch()
        filesToClear = set()
        try:
            previousCommitHash = self.getBranchCommit(previousBranch)
            if previousCommitHash:
                prevCommitObject = self.loadObject(previousCommitHash)
                prevCommit = Commit.fromContent(prevCommitObject.content)
                if prevCommit.treeHash:
                    filesToClear = self.getFilesFromTreeRecursive(prevCommit.treeHash)
        except Exception:
            filesToClear = set()

        # created/moved to a new branch
        branchFile = self.headsDir / branch
        if not branchFile.exists():
            if createBranch:
                if previousCommitHash:
                    self.set_branch_commit(branch, previousCommitHash)
                    print(f"Created new branch {branch}")
                else:
                    print("No commits yet, cannot create a branch")
                    return
            else:
                print(f"Branch '{branch}' not found.")
                print("Use 'python3 main.py checkout -b {branch}' to create and switch to a new branch.")
                return
            
        self.headFile.write_text(f"ref: refs/heads/{branch}\n")

        # restore working directory
        self.restoreWorkingDirectory(branch, filesToClear)
        print(f"Switched to branch {branch}")

    def restoreTree(self, treeHash: str, path: Path):
        treeObj = self.loadObject(treeHash)
        tree = Tree.fromContent(treeObj.content)
        for mode, name, objHash in tree.entries:
            filePath = path / name
            if mode.startswith("100"):
                blobObj = self.loadObject(objHash)
                blob = Blob(blobObj.content)
                filePath.write_bytes(blob.content)
            elif mode.startswith("400"):
                filePath.mkdir(exist_ok=True)
                self.restoreTree(objHash, filePath)

    def restoreWorkingDirectory(self, branch: str, filesToClear: set[str]):
        targetCommitHash = self.getBranchCommit(branch)
        if not targetCommitHash:
            return

        # remove files tracked by previous branch
        for relPath in sorted(filesToClear):
            filePath = self.path / relPath
            try:
                if filePath.is_file():
                    filePath.unlink()
                # Uncomment if you want this functionality (removing empty directories)
                # elif filePath.is_dir():
                #     if not any(filePath.iterdir()):
                #         filePath.rmdir()
            except Exception:
                pass

        targetCommitObj = self.loadObject(targetCommitHash)
        targetCommit = Commit.fromContent(targetCommitObj.content)

        if targetCommit.treeHash:
            self.restoreTree(targetCommit.treeHash, self.path)

        self.saveIndex({})

    def branch(self, branchName: str, delete: bool = False):
        # delete
        if delete and branchName:
            branchFile = self.headsDir / branchName
            if branchFile.exists():
                branchFile.unlink()
                print(f"Deleted branch {branchName}")
            else:
                print(f"Branch {branchName} not found")

            return

        currentBranch = self.getCurrentBranch()
        if branchName:
            currentCommit = self.getBranchCommit(currentBranch)
            if currentCommit:
                self.set_branch_commit(branchName, currentCommit)
                print(f"Created branch {branchName}")
            else:
                print(f"No commits yet, cannot create a new branch")
        else:
            branches = []
            for branchFile in self.headsDir.iterdir():
                if branchFile.is_file() and not branchFile.name.startswith("."):
                    branches.append(branchFile.name)

            for branch in sorted(branches):
                currentMarker = "* " if branch == currentBranch else "  "
                print(f"{currentMarker}{branch}")

    def log(self, maxCount: int = 10):
        currentBranch = self.getCurrentBranch()
        commitHash = self.getBranchCommit(currentBranch)

        if not commitHash:
            print("No commits yet!")
            return

        count = 0
        while commitHash and count < maxCount:
            commitObj = self.loadObject(commitHash)
            commit = Commit.fromContent(commitObj.content)

            print(f"Commit {commitHash}")
            print(f"Author: {commit.author}")
            print(f"Date: {time.ctime(commit.timestamp)}")
            print(f"\n    {commit.message}\n")

            commitHash = commit.parentHashes[0] if commit.parentHashes else None
            count += 1

    def buildIndexFromTree(self, treeHash: str, prefix: str = ""):
        index = {}
        try:
            treeObj = self.loadObject(treeHash)
            tree = Tree.fromContent(treeObj.content)
            # list<tuple<str, str, str>>
            for mode, name, objHash in tree.entries:
                fullName = f"{prefix}{name}"
                if mode.startswith("100"):
                    index[fullName] = objHash
                elif mode.startswith("400"):
                    subindex = self.buildIndexFromTree(objHash, f"{fullName}/")

                    index.update(subindex)
        except Exception as e:
            print(f"Warning: Could not read tree {treeHash}: {e}")

        return index

    def getAllFiles(self) -> List[Path]:
        files = []

        for item in self.path.rglob("*"):
            if ".pygit" in item.parts:
                continue

            if item.is_file():
                files.append(item)

        return files

    def status(self):
        # what branch we are on
        currentBranch = self.getCurrentBranch()
        print(f"On branch {currentBranch}")
        index = self.loadIndex()
        currentCommitHash = self.getBranchCommit(currentBranch)

        # build the index of the latest commit
        lastIndexFiles = {}
        if currentCommitHash:
            try:
                commitObj = self.loadObject(currentCommitHash)
                commit = Commit.fromContent(commitObj.content)
                if commit.treeHash:
                    lastIndexFiles = self.buildIndexFromTree(commit.treeHash)
            except:
                lastIndexFiles = {}

        # figure out all the files present within the working directory
        workingFiles = {}  # file name -> hash
        for item in self.getAllFiles():
            relPath = str(item.relative_to(self.path))

            try:
                content = item.read_bytes()
                blob = Blob(content)
                workingFiles[relPath] = blob.hash()
            except:
                continue

        stagedFiles = []
        unstagedFiles = []
        untrackedFiles = []
        deletedFiles = []

        # what files are staged for commit
        for filePath in set(index.keys()) | set(lastIndexFiles.keys()):
            indexHash = index.get(filePath)
            lastIndexHash = lastIndexFiles.get(filePath)

            if indexHash and not lastIndexHash:
                stagedFiles.append(("new file", filePath))
            elif indexHash and lastIndexHash and indexHash != lastIndexHash:
                stagedFiles.append(("modified", filePath))

        if stagedFiles:
            print("\nChanges to be committed:")
            for stageStatus, filePath in sorted(stagedFiles):
                print(f"   {stageStatus}: {filePath}")

        # what files have modified but not staged
        for filePath in workingFiles:
            if filePath in index:
                if workingFiles[filePath] != index[filePath]:
                    unstagedFiles.append(filePath)

        if unstagedFiles:
            print("\nChanges not staged for commit:")
            for filePath in sorted(unstagedFiles):
                print(f"   modified: {filePath}")

        # what files are untracked
        for filePath in workingFiles:
            if filePath not in index and filePath not in lastIndexFiles:
                untrackedFiles.append(filePath)

        if untrackedFiles:
            print("\nUntracked files:")
            for filePath in sorted(untrackedFiles):
                print(f"   {filePath}")

        # what files have been deleted
        for filePath in index:
            if filePath not in workingFiles:
                deletedFiles.append(filePath)

        if deletedFiles:
            print("\nDeleted files:")
            for filePath in sorted(deletedFiles):
                print(f"   deleted: {filePath}")

        if (
            not stagedFiles
            and not unstagedFiles
            and not deletedFiles
            and not untrackedFiles
        ):
            print("\nnothing to commit, working tree clean")


def main():
    parser = argparse.ArgumentParser(description="PyGit - A simple git clone!")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # init command
    initParser = subparsers.add_parser("init", help="Initialize a new repository")

    # add command
    addParser = subparsers.add_parser(
        "add", help="Add files and directories to the staging area"
    )
    addParser.add_argument("paths", nargs="+", help="Files and directories to add")

    # commit command
    commitParser = subparsers.add_parser("commit", help="Create a new commit")
    commitParser.add_argument(
        "-m",
        "--message",
        help="Commit message",
        required=True,
    )
    commitParser.add_argument(
        "--author",
        help="Author name and email",
    )

    # checkout command
    checkoutParser = subparsers.add_parser("checkout", help="Move/Create a new branch")
    checkoutParser.add_argument("branch", help="Branch to switch to")
    checkoutParser.add_argument(
        "-b",
        "--create-branch",
        dest="createBranch",
        action="store_true",
        help="Create and switch to a new branch",
    )

    # branch command
    branchParser = subparsers.add_parser("branch", help="List or manage branches")
    branchParser.add_argument("name", nargs="?")
    branchParser.add_argument(
        "-d",
        "--delete",
        action="store_true",
        help="Delete the branch",
    )

    # log command
    logParser = subparsers.add_parser("log", help="Show commit history")
    logParser.add_argument(
        "-n",
        "--max-count",
        dest="maxCount",
        type=int,
        default=10,
        help="Limit commits shown",
    )

    # status command
    statusParser = subparsers.add_parser("status", help="Show repository status")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    repo = Repository()
    try:
        if args.command == "init":
            if not repo.init():
                print("Repository already exists")
                return
        elif args.command == "add":
            if not repo.gitDir.exists():
                print("Not a git repository")
                return

            for path in args.paths:
                repo.addPath(path)
        elif args.command == "commit":
            if not repo.gitDir.exists():
                print("Not a git repository")
                return

            author = args.author or "PyGit user <user@pygit.com>"
            repo.commit(args.message, author)
        elif args.command == "checkout":
            if not repo.gitDir.exists():
                print("Not a git repository")
                return
            repo.checkout(args.branch, args.createBranch)
        elif args.command == "branch":
            if not repo.gitDir.exists():
                print("Not a git repository")
                return

            repo.branch(args.name, args.delete)
        elif args.command == "log":
            if not repo.gitDir.exists():
                print("Not a git repository")
                return

            repo.log(args.maxCount)
        elif args.command == "status":
            if not repo.gitDir.exists():
                print("Not a git repository")
                return

            repo.status()

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
