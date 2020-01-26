"""Register with PNP server and wait for remote peers to connect."""
# import argparse
import asyncio
import logging
import sys
import json
import requests
from typing import Any
import coloredlogs

# from aiortc import RTCIceCandidate, RTCSessionDescription
from peerjs.peer import Peer, PeerOptions
from peerjs.peerroom import PeerRoom
from peerjs.util import util
from peerjs.enums import ConnectionEventType, PeerEventType

print(sys.version)

log = logging.getLogger(__name__)

LOG_LEVEL = logging.INFO

peer = None
myPeerId = None
AMBIANIC_PNP_HOST = 'ambianic-pnp.herokuapp.com'  # 'localhost'
AMBIANIC_PNP_PORT = 443  # 9779
AMBIANIC_PNP_SECURE = True  # False
time_start = None
peerConnectionStatus = None
discoveryLoop = None


# async def _consume_signaling(pc, signaling):
#     while True:
#         obj = await signaling.receive()
#         if isinstance(obj, RTCSessionDescription):
#             await pc.setRemoteDescription(obj)
#             if obj.type == "offer":
#                 # send answer
#                 await pc.setLocalDescription(await pc.createAnswer())
#                 await signaling.send(pc.localDescription)
#         elif isinstance(obj, RTCIceCandidate):
#             pc.addIceCandidate(obj)
#         elif obj is None:
#             print("Exiting")
#             break


async def join_peer_room(peer=None):
    """Join a peer room with other local peers."""
    # first try to find the remote peer ID in the same room
    myRoom = PeerRoom(peer)
    log.debug('Fetching room members...')
    peerIds = await myRoom.getRoomMembers()
    log.debug('myRoom members %r', peerIds)


def _setPnPServiceConnectionHandlers(peer=None):
    assert peer
    global myPeerId
    @peer.on(PeerEventType.Open)
    async def peer_open(id):
        log.warning('Peer signaling connection open.')
        global myPeerId
        # Workaround for peer.reconnect deleting previous id
        if peer.id is None:
            log.warning('pnpService: Received null id from peer open')
            peer.id = myPeerId
        else:
            if myPeerId != peer.id:
                log.info(
                    'PNP Service returned new peerId. Old %s, New %s',
                    myPeerId,
                    peer.id
                    )
            myPeerId = peer.id
        log.info('myPeerId: %s', peer.id)

    @peer.on(PeerEventType.Disconnected)
    async def peer_disconnected(peerId):
        global myPeerId
        log.info('pnpService: Peer %s disconnected from server.', peerId)
        # Workaround for peer.reconnect deleting previous id
        if not peer.id:
            log.info('BUG WORKAROUND: Peer lost ID. '
                     'Resetting to last known ID.')
            peer._id = myPeerId
        peer._lastServerId = myPeerId
        await peer.reconnect()

    @peer.on(PeerEventType.Close)
    def peer_close():
        # peerConnection = null
        log.warning('Peer connection closed')

    @peer.on(PeerEventType.Error)
    def peer_error(err):
        log.exception('Peer error %s', err)
        log.warning('peerConnectionStatus %s', peerConnectionStatus)
        # retry peer connection in a few seconds
        # loop = asyncio.get_event_loop()
        # loop.call_later(3, pnp_service_connect)

    # remote peer tries to initiate connection
    @peer.on(PeerEventType.Connection)
    async def peer_connection(peerConnection):
        log.warning('Remote peer trying to establish connection')
        _setPeerConnectionHandlers(peerConnection)


async def _fetch(url: str = None, method: str = 'GET') -> Any:
    if method == 'GET':
        response = requests.get(url)
        response_content = response.content
        # response_content = {'name': 'Ambianic-Edge', 'version': '1.24.2020'}
        # rjson = json.dumps(response_content)
        return response_content
    else:
        raise NotImplementedError(
            f'HTTP method ${method} not implemented.'
            ' Contributions welcome!')


def _setPeerConnectionHandlers(peerConnection):
    @peerConnection.on(ConnectionEventType.Open)
    async def pc_open():
        log.warning('Connected to: %s', peerConnection.peer)

    # Handle incoming data (messages only since this is the signal sender)
    @peerConnection.on(ConnectionEventType.Data)
    async def pc_data(data):
        log.warning('data received from remote peer \n%r', data)
        request = json.loads(data)
        log.warning('webrtc peer: http proxy request: \n%r', request)
        response_content = await _fetch(**request)
        log.warning('Answering request: \n%r '
                    'response size: \n%r',
                    request, len(response_content))
        await peerConnection.send(response_content)

    @peerConnection.on(ConnectionEventType.Close)
    async def pc_close():
        log.info('Connection to remote peer closed')


async def pnp_service_connect() -> Peer:
    """Create a Peer instance and register with PnP signaling server."""
    # if connection to pnp service already open, then nothing to do
    global peer
    if peer and peer.open:
        log.info('peer already connected')
        return
    # Create own peer object with connection to shared PeerJS server
    log.info('creating peer')
    # If we already have an assigned peerId, we will reuse it forever.
    # We expect that peerId is crypto secure. No need to replace.
    # Unless the user explicitly requests a refresh.
    global myPeerId
    log.info('last saved myPeerId %s', myPeerId)
    new_token = util.randomToken()
    log.info('Peer session token %s', new_token)
    options = PeerOptions(
        host=AMBIANIC_PNP_HOST,
        port=AMBIANIC_PNP_PORT,
        secure=AMBIANIC_PNP_SECURE,
        token=new_token
    )
    peer = Peer(id=myPeerId, peer_options=options)
    log.info('pnpService: peer created with id %s , options: %r',
             peer.id,
             peer.options)
    await peer.start()
    log.info('peer activated')
    _setPnPServiceConnectionHandlers(peer)
    await make_discoverable(peer=peer)


async def make_discoverable(peer=None):
    """Enable remote peers to find and connect to this peer."""
    assert peer
    while True:
        log.debug('Making peer discoverable.')
        try:
            # check if the websocket connection
            # to the signaling server is alive
            if peer.open:
                await join_peer_room(peer=peer)
            else:
                log.info('Peer not connected to signaling server. '
                         'Will retry in a bit.')
                if peer.disconnected:
                    log.info('Peer disconnected. Will try to reconnect.')
                    await peer.reconnect()
                else:
                    log.info('Peer still establishing connection. %r', peer)
        except Exception as e:
            log.exception('Unable to join room. '
                          'Will retry in a few moments. '
                          'Error %r', e)
        await asyncio.sleep(3)


def _config_logger():
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    format_cfg = '%(asctime)s %(levelname)-4s ' \
        '%(pathname)s.%(funcName)s(%(lineno)d): %(message)s'
    datefmt_cfg = '%Y-%m-%d %H:%M:%S'
    fmt = logging.Formatter(fmt=format_cfg,
                            datefmt=datefmt_cfg, style='%')
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(LOG_LEVEL)
    ch.setFormatter(fmt)
    root_logger.handlers = []
    root_logger.addHandler(ch)
    coloredlogs.install(level=LOG_LEVEL, fmt=format_cfg)


if __name__ == "__main__":
    # args = None
    # parser = argparse.ArgumentParser(description="Data channels ping/pong")
    # parser.add_argument("role", choices=["offer", "answer"])
    # parser.add_argument("--verbose", "-v", action="count")
    # add_signaling_arguments(parser)
    # args = parser.parse_args()
    # if args.verbose:
    _config_logger()
    # add formatter to ch
    log.debug('Log level set to debug')
    # signaling = create_signaling(args)
    # signaling = AmbianicPnpSignaling(args)
    # pc = RTCPeerConnection()
    # if args.role == "offer":
    #     coro = _run_offer(pc, signaling)
    # else:
    #     coro = _run_answer(pc, signaling)
    coro = pnp_service_connect
    # run event loop
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(coro())
    except KeyboardInterrupt:
        log.info('KeyboardInterrupt detected. Exiting...')
        pass
    finally:
        if peer:
            loop.run_until_complete(peer.destroy())
        # loop.run_until_complete(pc.close())
        # loop.run_until_complete(signaling.close())
