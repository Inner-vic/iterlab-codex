"""Protocol fixture; never used by the application or real acceptance."""
import json
import sys

for line in sys.stdin:
    packet=json.loads(line)
    method=packet.get('method')
    if 'id' not in packet:
        continue
    if method=='crash':
        sys.exit(3)
    result={'userAgent':'test'} if method=='initialize' else {'echo':packet.get('params')}
    print(json.dumps({'id':packet['id'],'result':result}),flush=True)
    if method=='emit':
        print(json.dumps({'method':'turn/completed','params':{'threadId':'fixture','turn':{'id':'t','status':'completed'}}}),flush=True)
