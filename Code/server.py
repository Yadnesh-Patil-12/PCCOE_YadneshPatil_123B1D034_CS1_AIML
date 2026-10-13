"""Entry point:  python server.py [--host 127.0.0.1] [--port 8000]"""
import argparse

from hldassist.app import serve

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="HLD Assistant - AUTOSAR HLD document analysis")
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    a = ap.parse_args()
    serve(a.host, a.port)
