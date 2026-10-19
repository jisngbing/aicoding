"""Expose file operations while DeepAgents middleware enforces permissions."""
from deepagents.backends.protocol import BackendProtocol


class FileOnlyBackend(BackendProtocol):
    """Adapt an initialized backend without advertising shell execution."""

    def __init__(self, backend: BackendProtocol):
        self.backend = backend

    def ls(self, path):
        return self.backend.ls(path)

    def read(self, file_path, offset=0, limit=2000):
        return self.backend.read(file_path, offset, limit)

    def write(self, file_path, content):
        return self.backend.write(file_path, content)

    def edit(self, file_path, old_string, new_string, replace_all=False):
        return self.backend.edit(file_path, old_string, new_string, replace_all)

    def glob(self, pattern, path=None):
        return self.backend.glob(pattern, path)

    def grep(self, pattern, path=None, glob=None, *, max_count=None):
        # The course LocalShellBackend predates the max_count keyword.
        result = self.backend.grep(pattern, path, glob)
        if max_count is not None and result.matches is not None:
            result.matches = result.matches[:max_count]
        return result

    def upload_files(self, files):
        return self.backend.upload_files(files)

    def download_files(self, paths):
        return self.backend.download_files(paths)
