# Imports
from __future__ import annotations
import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Dict
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
        super().__init__('BLOB', content)

    def get_content(self) -> bytes:
        return self.content

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

def main():
    parser = argparse.ArgumentParser( description = "A simple Git Clone." )

    subParsers = parser.add_subparsers( dest = "command", help = "Available commands" )

    # init command
    initParser = subParsers.add_parser( "init", help = "Initialize a new repository" )

    # add command
    addParser = subParsers.add_parser( "add", help = "Add files and directories to stage the changes." )
    addParser.add_argument("paths", nargs = "+", help = "Files and directories to add.")

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

    except Exception as e:
        print(f"Error occured: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()