#!/usr/bin/env python3
"""Isolated HTTP worker: caller enforces a hard subprocess deadline."""
import json
import os
import sys
import urllib.error
import urllib.request


def main():
    payload=json.load(sys.stdin)
    key=os.environ['OPENROUTER_API_KEY']
    request=urllib.request.Request('https://openrouter.ai/api/v1/chat/completions',
        data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(request,timeout=90) as response:raw=json.load(response)
        print(json.dumps({'response':raw}))
    except urllib.error.HTTPError as e:
        # Never expose request headers or arbitrary provider error text.
        print(json.dumps({'error_type':'HTTPError','http_status':e.code}))
    except Exception as e:
        print(json.dumps({'error_type':type(e).__name__}))


if __name__=='__main__':main()
