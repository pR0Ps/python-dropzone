#!/usr/bin/env python

from collections import defaultdict
import io
from threading import Lock
import time
from shutil import copyfileobj
from pathlib import Path
from uuid import uuid4

from flask import Flask, render_template, request
from werkzeug.utils import secure_filename

DEFAULT_FILENAME = "unnamed.dat"

app = Flask(__name__)

UPLOAD_PATH = Path(app.root_path) / "upload"
TMP_PATH = UPLOAD_PATH / ".tmp"

UPLOAD_PATH.mkdir(parents=True, exist_ok=True)
TMP_PATH.mkdir(parents=True, exist_ok=True)

lock = Lock()
progress = defaultdict(int)

# TODO: scheduled task/timeout for failed partial uploads to delete temp files

@app.route("/", methods=["GET", "PUT", "POST"])
def index():
    if request.method == "GET":
        return render_template('index.html')

    if "file" not in request.files:
        return ("No data to upload", 400)

    if request.method == "PUT":
        # Since this is a PUT, we expect chunks with the data required to make
        # the request idempotent
        try:
            uuid = request.form["dzuuid"]
            offset = int(request.form["dzchunkbyteoffset"])
            total_size = int(request.form["dztotalfilesize"])
            chunk_id = int(request.form["dzchunkindex"])
            total_chunks = int(request.form["dztotalchunkcount"])
        except Exception as e:
            return (f"Missing/invalid parameter: {e.__class__.__qualname__}: {e}", 400)
    elif request.method == "POST":
        if "dzuuid" in request.form:
            return ("Multipart uploads must be done using PUT requests", 400)

        # Not a chunked multipart upload - create dummy values so we can follow
        # the same logic below
        uuid = str(uuid4())
        offset = 0
        total_size = 0
        chunk_id = 0
        total_chunks = 1
    else:
        return (f"Method '{request.method}' not allowed", 405)

    data = request.files["file"]

    # Write the chunk into the target temp file in a thread-safe way
    try:
        path = TMP_PATH / secure_filename(uuid)
        path.touch(exist_ok=True)
        with path.open(mode="r+b") as fp:
            fp.truncate(total_size)
            fp.seek(offset, io.SEEK_SET)
            copyfileobj(data.stream, fp)
    except Exception as e:
        return (f"Failed to write to file: {e.__class__.__qualname__}: {e}", 500)

    # If all the chunks have been uploaded, move the completed file into place
    # TODO: scope the lock per-uuid?
    with lock:
        # Progress is tracked using an int that is used as a big bitfield.
        # Each chunk sets a bit based on its number. Once all the bits are set, the download is complete
        # TODO: use bytes instead?
        progress[uuid] |= 1 << chunk_id
        if progress[uuid] == (1 << total_chunks) - 1:
            del progress[uuid]
            filename = secure_filename(data.filename or DEFAULT_FILENAME)
            path.rename(UPLOAD_PATH / f"{time.time()}_{filename}")

    return ("", 200)


if __name__ == '__main__':
    app.run(debug=True, host="0.0.0.0", port=5000)
