# Imports
import argparse
import json
import sys
from pathlib import Path

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
        
        self.indexFile.write_text(json.dumps({}, indent=2))

        print(f"Initialized empty PyGit Repository in {self.gitDir}")
        return True

def main():
    parser = argparse.ArgumentParser( description = "A simple Git Clone." )

    subParsers = parser.add_subparsers( dest = "command", help = "Available commands" )

    # init command
    init_parser = subParsers.add_parser( "init", help = "Initialize a new repository" )
    
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return
    
    try:
        if args.command == "init":
            repo = Repository()
            if not repo.init():
                print("Repository already exists.")
                return

    except Exception as e:
        print(f"Error occured: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()