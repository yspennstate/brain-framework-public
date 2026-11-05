"""query_embed_proc.py - encode one query in a process of its own, for brain.py recall.

onnxruntime holds the GIL for the whole of InferenceSession's build (about 0.65 s on this laptop, measured 13 Oct 2026:
a spinning thread was shut out 96-97% of it), so building the query encoder on a thread inside recall froze recall's
main thread for that long and slowed both threads' imports. Here the build runs in its own interpreter.

Reads the text (UTF-8) from stdin, all of it, before anything else: brain.py writes it the moment this process starts,
so a long text never keeps the writer waiting on a pipe this process is too busy to read. Writes b"V" followed by the
float32 vector's little-endian bytes, or b"N" when query_embed.embed returns None. The vector is query_embed.embed's
own, byte for byte. query_embed.py itself is left unchanged on purpose: fastrecall pins its sha256.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    text = sys.stdin.buffer.read().decode("utf-8")
    import query_embed
    v = query_embed.embed(text)
    out = sys.stdout.buffer
    out.write(b"N" if v is None else b"V" + v.astype("<f4").tobytes())
    out.flush()
    return 0


if __name__ == "__main__":
    # os._exit after the flush: onnxruntime 1.31.1's C++ teardown at exit() can spin forever (seen in two recalls on
    # 31 Oct), and brain.py would wait out its 20 s for an answer already written. Every path, not just the good one: a
    # recall that died or gave up before reading leaves a closed pipe, the write raises, and the normal exit spun for
    # 1 h 44 min at 60-90% of a core, parented to launchd (pid 23248, 3 Nov; sample showed main in exit()).
    try:
        code = main()
    except BaseException:
        code = 1
    os._exit(code)
