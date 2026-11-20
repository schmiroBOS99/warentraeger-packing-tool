"""PyInstaller entry point. Kept at the project root (rather than pointing
PyInstaller at app/main.py directly) so the 'app' package resolves both at
build-analysis time and at runtime in the frozen exe: PyInstaller adds this
script's own directory to sys.path, and that directory must be the project
root for `from app.gui import App` to work."""
from app.main import main

if __name__ == "__main__":
    main()
