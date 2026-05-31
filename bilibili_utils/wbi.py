"""
B站 WBI 签名工具
"""
import hashlib
from urllib.parse import urlencode

MIXIN_KEY_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 20, 44, 54, 28, 14, 34, 56, 4, 25, 63, 57, 62, 51, 30,
    36, 24, 6, 64, 46, 11, 60, 51, 30, 36, 24, 6, 64, 46, 11, 60, 51, 30, 36,
    24, 6, 64, 46, 11, 60, 51, 30, 36, 24, 6, 64, 46, 11, 60, 51, 30, 36, 24,
    6, 64, 46, 11, 60
]

def mixin_key(orig: str) -> str:
    result = []
    for i in MIXIN_KEY_TAB:
        if i < len(orig):
            result.append(orig[i])
    return ''.join(result)

def get_wbi_sign(params: dict, img_key: str, sub_key: str) -> str:
    mil = mixin_key(img_key + sub_key)
    half_len = len(mil) // 2
    query_str = urlencode(sorted(params.items()), safe='/:?=')
    sign_str = mil[:half_len] + query_str + mil[half_len:]
    return hashlib.md5(sign_str.encode()).hexdigest()