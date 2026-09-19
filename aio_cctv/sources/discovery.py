"""Temukan kamera di LAN: ONVIF WS-Discovery lalu fallback scan port 554."""
import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse


def onvif_discover(timeout: int = 3) -> list[dict]:
    from wsdiscovery import QName
    from wsdiscovery.discovery import ThreadedWSDiscovery as WSDiscovery

    wsd = WSDiscovery()
    wsd.start()
    try:
        nvt = QName("http://www.onvif.org/ver10/network/wsdl", "NetworkVideoTransmitter")
        services = wsd.searchServices(types=[nvt], timeout=timeout)
        found = []
        for s in services:
            for xaddr in s.getXAddrs():
                u = urlparse(xaddr)
                found.append({"host": u.hostname, "onvif_port": u.port or 80, "xaddr": xaddr})
        return found
    finally:
        wsd.stop()


def onvif_stream_uris(host: str, port: int, user: str, password: str) -> list[dict]:
    """Minta URL RTSP resmi dari kamera (Media.GetStreamUri) — paling andal bila ONVIF aktif."""
    from onvif import ONVIFCamera

    cam = ONVIFCamera(host, port, user, password)
    media = cam.create_media_service()
    uris = []
    for prof in media.GetProfiles():
        req = media.create_type("GetStreamUri")
        req.ProfileToken = prof.token
        req.StreamSetup = {"Stream": "RTP-Unicast", "Transport": {"Protocol": "RTSP"}}
        uri = media.GetStreamUri(req).Uri
        res = prof.VideoEncoderConfiguration.Resolution if prof.VideoEncoderConfiguration else None
        uris.append({"profile": prof.Name, "uri": uri,
                     "resolution": (res.Width, res.Height) if res else None})
    return uris


def scan_rtsp_port(cidr: str, port: int = 554, timeout: float = 0.4) -> list[str]:
    """Fallback untuk kamera tanpa ONVIF: cari host yang membuka port RTSP."""
    def check(ip: str) -> str | None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return ip if s.connect_ex((ip, port)) == 0 else None

    hosts = [str(h) for h in ipaddress.ip_network(cidr, strict=False).hosts()]
    with ThreadPoolExecutor(max_workers=64) as ex:
        return [ip for ip in ex.map(check, hosts) if ip]
