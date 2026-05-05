# Imports
from __future__ import annotations
import argparse
import hashlib
import json
import sys
from pathlib import Path
import time
from typing import Dict, List, Tuple
import zlib

class GitObject:
    def __init__(self, objType: str, content: bytes):
        self.type = objType
        self.content = content
    
    def hash(self) -> str:
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
        content = decompressed[nullIdx + 1:]

        objType, _ = header.split(" ")

        return cls(objType, content)
    
class BLOB(GitObject):
    def __init__(self, content: bytes):
        super().__init__("BLOB", content)

    def get_content(self) -> bytes:
        return self.content
    
class Tree(GitObject):
    def __init__(self, entries: List[Tuple[str, str, str]] = None):
        self.entries = entries or []
        content = self._serializeEntries()
        super().__init__("Tree", content)

    def _serializeEntries(self) -> bytes:
        content = b""
        for mode, name, objHash in sorted(self.entries):
            content += f"{mode} {name}\0".encode()
            content += bytes.fromhex(objHash)

        return content
    
    def addEntry(self, mode: str, name: str, objHash: str):
        self.entries.append((mode, name, objHash))
        self.content = self._serializeEntries()
    
    @classmethod
    def fromContent(cls, content: bytes) -> Tree:
        tree = cls()
        i = 0

        while i < len(content):
            nullIdx = content.find(b'\0', i)
            if nullIdx == -1:
                break

            modeName = content[i : nullIdx].decode()
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

        content = self._serializeCommit()
        super().__init__("Commit", content)

    def _serializeCommit(self):
        lines = [f"tree {self.treeHash}"]
        for parent in self.parentHashes:
            lines.append(f"parent {parent}")
            
        lines.append(f"author {self.author} {self.timestamp} +000")
        lines.append(f"committer {self.committer} {self.timestamp} +000")
        lines.append("")
        lines.append(self.message)

        return "\n".join(lines).encode()
    
    @classmethod
    def fromContent(cls, content: bytes) -> Commit:
        lines = content.decode().split(" ")
        treeHash = None
        parentHashes = []
        author = None
        committer = None
        msgStart = 0

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
                msgStart = i + 1
                break

        msg = "\n".join(lines[msgStart:])
        commit = cls(treeHash, parentHashes, author, committer, msg, timestamp)

        return commit

# Main class "Repository":
class Repository:
    def __init__(self, path = "."):
        self.path = Path(path).resolve()
        self.gitDir = self.path / ".pygit"

        # .git/objects path
        self.objectsDir = self.gitDir / "objects"

        # .git/refs path
        self.refsDir = self.gitDir / "refs"
        self.headsDir = self.refsDir / "heads"

        # .git/HEAD File
        self.headFile = self.gitDir / "HEAD"

        # .git/index File
        self.indexFile = self.gitDir / "index"

    def init(self) -> bool:
        if self.gitDir.exists():
            return False
        
        self.gitDir.mkdir()
        self.objectsDir.mkdir()
        self.refsDir.mkdir()
        self.headsDir.mkdir()

        # Create initial HEAD pointing to a branch
        self.headFile.write_text("ref: refs/heads/master\n")

        self.saveIndex({})
        
        print(f"Initialized empty PyGit Repository in {self.gitDir}")
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
        self.indexFile.write_text(json.dumps(index, indent = 2))

    def addFile(self, path: str):
        fullPath = self.path / path
        if not fullPath.exists():
            raise FileNotFoundError(f"Path {path} was not found!")
        
        # Read file content:
        content = fullPath.read_bytes()

        # Create BLOB Object:
        blob = BLOB(content)

        # Storing 'blob' in .git/objects
        blobHash = self.storeObject(blob)

        # Update index to include the file
        index = self.loadIndex()
        index[path] = blobHash

        self.saveIndex(index)

        print(f"Added {path}")
    
    def addDirectory(self, path: str):
        fullPath = self.path / path
        if not fullPath.exists():
            raise FileNotFoundError(f"Directory {path} was not found!")
        if not fullPath.is_dir():
            raise ValueError(f"{path} is not a directory!")
        
        index = self.loadIndex()
        addCount = 0
        # Recursively traverse the directory
        for filePath in fullPath.rglob("*"):
            if filePath.is_file():
                if ".pygit" in filePath.parts or ".git" in filePath.parts:
                    continue

                content = filePath.read_bytes()
                blob = BLOB(content)
                blobHash = self.storeObject(blob)

                relPath = str(filePath.relative_to(self.path))
                index[relPath] = blobHash

                addCount += 1

        self.saveIndex(index)

        if addCount >= 0:
            print(f"Added {addCount} file(s) from directory \"{path}\"")
        else:
            print(f"Directory \"{path}\" already is up to date!")

    def addPath(self, path: str):
        fullPath = self.path / path
        
        if not fullPath.exists():
            raise FileNotFoundError(f"Path {path} was not found!")
        
        # Case-1: File
        if fullPath.is_file():
            self.addFile(path)
        elif fullPath.is_dir():
            self.addDirectory(path)
        else:
            raise ValueError(f"{path} is neither a file nor a directory.")

    def loadObject(self, objHash: str) -> GitObject:
        objDir = self.objectsDir / objHash[:2]
        objFile = objDir / objHash[2:]

        if not objFile.exists():
            raise FileNotFoundError(f"Object {objHash} not found!")
        
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
                files[parts[0]] = blobHash
            else:
                dirName = parts[0]
                if not dirName in dirs:
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
                    subTreeHash = createTreeRecursive(blobHash)
                    tree.addEntry("40000", name, subTreeHash)
            
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
        
        return "HEAD"

    def getBranchCommit(self, branch: str):
        branchFile = self.headsDir / branch
        
        if branchFile.exists():
            return branchFile.read_text().strip()

        return None

    def setBranchCommit(self, branch: str, commitHash: str):
        branchFile = self.headsDir / branch
        branchFile.write_text(commitHash + "\n")

    def commit(self, message: str, author: str):
        treeHash = self.createTreeFromIndex()

        currentBranch = self.getCurrentBranch()
        parentCommit = self.getBranchCommit(currentBranch)
        parentHashes = [parentCommit] if parentCommit else []

        index = self.loadIndex()
        if not index:
            print("Nothing to commit, working tree is clean!")
            return None
        
        if parentCommit:
            parentGitCommitObj = self.loadObject(parentCommit)
            parentCommitData = Commit.fromContent(parentGitCommitObj.content)

            if treeHash == parentCommitData.treeHash:
                print("Nothing to commit, working tree clean!")
                return None

        commit = Commit(treeHash, parentHashes, author, author, message)
        commitHash = self.storeObject(commit)

        self.setBranchCommit(currentBranch, commitHash)
        self.saveIndex({})
        print(f"Created commit {commitHash} on the branch {currentBranch}")
        return commitHash

def main():
    parser = argparse.ArgumentParser( description = "A simple Git Clone." )

    subParsers = parser.add_subparsers( dest = "command", help = "Available commands" )

    # init command
    initParser = subParsers.add_parser( "init", help = "Initialize a new repository" )

    # add command
    addParser = subParsers.add_parser( "add", help = "Add files and directories to stage the changes." )
    addParser.add_argument("paths", nargs = "+", help = "Files and directories to add.")

    # commit command
    commitParser = subParsers.add_parser( "commit", help = "Commit changes to repository." )
    commitParser.add_argument("-m", "--message", help = "Commit message.", required = True)
    commitParser.add_argument("--author", help = "Author of repo.")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return
    
    repo = Repository()

    try:
        if args.command == "init":
            if not repo.init():
                print("Repository already exists.")
                return

        elif args.command == "add":
            if not repo.gitDir.exists():
                print("Not a git repository.")
                return
        
            for path in args.paths:
                repo.addPath(path)

        elif args.command == "commit":
            if not repo.gitDir.exists():
                print("Not a git repository.")
                return

            author = args.author or "PyGit User <user@pygit.io>"
            repo.commit(args.message, author)

    except Exception as e:
        print(f"Error occured: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()