"""智慧树加解密与签名工具（vendor 适配版）。

上游用 pycryptodome 的 Crypto.Cipher，本仓库改用**项目自带的纯 Python 实现
pyaes**（api/cipher.py 已在用），避免为单一平台引入额外二进制依赖；
Cipher 的对外接口（encrypt/decrypt/pad/unpad）与原实现完全一致。
"""

from base64 import b64encode, b64decode

import pyaes

IV = b"1g3qqdh4jvbskb9x"

HOME_KEY  = b"7q9oko0vqb3la20r"
VIDEO_KEY = b"qz632524n86i7fk9"
QA_KEY    = b"w1k5ntxecb8wmbc2"

class Cipher:
    def __init__(self, key:bytes=VIDEO_KEY, iv:bytes=IV):
        self.key = key
        self.iv = iv

    @staticmethod
    def pad(data):
        """PKCS#7 填充。注意按**字节**计算：原版按字符数算，遇到多字节字符
        （中文）会产出非 16 倍数的数据；调用方 json.dumps 默认 ensure_ascii
        输出纯 ASCII，两者结果一致，这里只是把边界补正。"""
        raw = data.encode("utf-8") if isinstance(data, str) else data
        n = 16 - len(raw) % 16
        return raw + bytes([n]) * n
    
    @staticmethod
    def unpad(data):
        raw = data if isinstance(data, bytes) else data.encode("utf-8")
        return raw[:-raw[-1]].decode("utf-8", errors="replace")

    def _run(self, raw: bytes, encrypt: bool) -> bytes:
        """按 16 字节分组跑 AES-CBC（pyaes 是逐块推进的接口）。"""
        cbc = pyaes.AESModeOfOperationCBC(self.key, self.iv)
        out = b""
        for i in range(0, len(raw), 16):
            block = raw[i:i + 16]
            out += cbc.encrypt(block) if encrypt else cbc.decrypt(block)
        return out

    def encrypt(self, data: str):
        return b64encode(self._run(self.pad(data), True)).decode()

    def decrypt(self, data: str):
        return self.unpad(self._run(b64decode(data), False))

class WatchPoint:
    def __init__(self, init:int=0):
        self.reset(init)

    def add(self, end:int, start:int=0):
        wp_interval = 2 # watch point record interval in seconds
        start = int(start) or self.last
        end = int(end)
        self.last = end
        for i in range(start, end+1)[::wp_interval]:
            self.wp.append(self.gen(i))

    def get(self):
        return ','.join(list(map(str,self.wp)))

    def reset(self, init:int=0):
        self.wp = [0,1]
        self.last = int(init) or 1
    
    @staticmethod
    def gen(time:int):
        return int(time//5+2)


def getEv(data:list, key:str="zzpttjd"):
    """
    * key:
      * d26666 -> "zzpttjd" (default)
      * d24444 -> "zhihuishu"
    """
    def gen():
        while True:
            for c in key:
                yield ord(c)
    gen = gen()
    data = ';'.join(map(str, data))
    ev = ""
    for c in data:
        tmp = hex(ord(c)^next(gen)).replace("0x", "")
        if len(tmp)<2:
            tmp = '0' + tmp
        ev += tmp[-4:] # actually -2 is fine, but their sauce code said -4
    return ev

def revEv(ev:str, key:str="zzpttjd"):
    """
    key: d26666->zzpttjd (default)
         d24444->zhihuishu
    """
    def gen():
        while True:
            for c in key:
                yield ord(c)
    gen = gen()
    ev = list(ev)
    ls = []
    ret = ""
    while ev:
        d2,d1 = ev.pop(),ev.pop()
        c = int(d1+d2, 16)
        ls.append(c)
    for c in ls[::-1]:
        ret += chr(c^next(gen))
    return ret
