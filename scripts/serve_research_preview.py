"""Local-only Pages preview with the same single-byte-range behavior as GitHub Pages."""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import re


class Handler(SimpleHTTPRequestHandler):
    def send_head(self):
        match = re.fullmatch(r"bytes=(\d+)-(\d+)", self.headers.get("Range", ""))
        if not match:
            return super().send_head()
        from pathlib import Path

        path = Path(self.translate_path(self.path))
        if not path.is_file():
            return super().send_head()
        start, end = map(int, match.groups())
        size = path.stat().st_size
        if not 0 <= start <= end < size:
            self.send_error(416)
            return None
        file = path.open("rb")
        file.seek(start)
        self.range_length = end - start + 1
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(str(path)))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(self.range_length))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        return file

    def copyfile(self, source, outputfile):
        length = getattr(self, "range_length", None)
        if length is None:
            return super().copyfile(source, outputfile)
        while length:
            body = source.read(min(length, 65536))
            if not body:
                break
            outputfile.write(body)
            length -= len(body)
        self.range_length = None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=4188)
    parser.add_argument("--directory", required=True)
    args = parser.parse_args()
    ThreadingHTTPServer(
        ("127.0.0.1", args.port), partial(Handler, directory=args.directory)
    ).serve_forever()
