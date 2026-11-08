import json,os,sys
log=os.environ['AEROKINETIC_NETWORK_AUDIT']
def record(event):
    with open(log,'a',encoding='utf-8')as f:f.write(json.dumps({'pid':os.getpid(),'event':event})+'\n')
def deny(event,args):
    if event in ('socket.connect','socket.getaddrinfo','socket.bind','socket.sendto'):
        record(event)
        raise RuntimeError('Network access denied during offline verification: '+event)
sys.addaudithook(deny)
sys._aerokinetic_network_denied=True
record('guard_installed')
