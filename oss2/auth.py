# -*- coding: utf-8 -*-

import hmac
import hashlib
import time

from . import utils
from .compat import urlquote, to_bytes

from .defaults import get_logger


class Auth(object):
    """用于保存用户AccessKeyId、AccessKeySecret，以及计算签名的对象。"""

    _subresource_key_set = frozenset(
        ['response-content-type', 'response-content-language',
         'response-cache-control', 'logging', 'response-content-encoding',
         'acl', 'uploadId', 'uploads', 'partNumber', 'group', 'link',
         'delete', 'website', 'location', 'objectInfo', 'objectMeta',
         'response-expires', 'response-content-disposition', 'cors', 'lifecycle',
         'restore', 'qos', 'referer', 'stat', 'bucketInfo', 'append', 'position', 'security-token',
         'live', 'comp', 'status', 'vod', 'startTime', 'endTime', 'x-oss-process',
         'symlink', 'callback', 'callback-var']
    )

    def __init__(self, access_key_id, access_key_secret):
        self.id = access_key_id.strip()
        self.secret = access_key_secret.strip()

    def _sign_request(self, req, bucket_name, key):
        req.headers['date'] = utils.http_date()

        signature = self.__make_signature(req, bucket_name, key)
        req.headers['authorization'] = "OSS {0}:{1}".format(self.id, signature)

    def _sign_url(self, req, bucket_name, key, expires):
        expiration_time = int(time.time()) + expires

        req.headers['date'] = str(expiration_time)
        signature = self.__make_signature(req, bucket_name, key)

        req.params['OSSAccessKeyId'] = self.id
        req.params['Expires'] = str(expiration_time)
        req.params['Signature'] = signature

        return req.url + '?' + '&'.join(_param_to_quoted_query(k, v) for k, v in req.params.items())

    def __make_signature(self, req, bucket_name, key):
        string_to_sign = self.__get_string_to_sign(req, bucket_name, key)

        get_logger().debug('string_to_sign={0}'.format(string_to_sign))

        h = hmac.new(to_bytes(self.secret), to_bytes(string_to_sign), hashlib.sha1)
        return utils.b64encode_as_string(h.digest())

    def __get_string_to_sign(self, req, bucket_name, key):
        resource_string = self.__get_resource_string(req, bucket_name, key)
        headers_string = self.__get_headers_string(req)

        content_md5 = req.headers.get('content-md5', '')
        content_type = req.headers.get('content-type', '')
        date = req.headers.get('date', '')
        return '\n'.join([req.method,
                          content_md5,
                          content_type,
                          date,
                          headers_string + resource_string])

    def __get_headers_string(self, req):
        headers = req.headers
        canon_headers = []
        for k, v in headers.items():
            lower_key = k.lower()
            if lower_key.startswith('x-oss-'):
                canon_headers.append((lower_key, v))

        canon_headers.sort(key=lambda x: x[0])

        if canon_headers:
            return '\n'.join(k + ':' + v for k, v in canon_headers) + '\n'
        else:
            return ''

    def __get_resource_string(self, req, bucket_name, key):
        if not bucket_name:
            return '/'
        else:
            return '/{0}/{1}{2}'.format(bucket_name, key, self.__get_subresource_string(req.params))

    def __get_subresource_string(self, params):
        if not params:
            return ''

        subresource_params = []
        for key, value in params.items():
            if key in self._subresource_key_set:
                subresource_params.append((key, value))

        subresource_params.sort(key=lambda e: e[0])

        if subresource_params:
            return '?' + '&'.join(self.__param_to_query(k, v) for k, v in subresource_params)
        else:
            return ''

    def __param_to_query(self, k, v):
        if v:
            return k + '=' + v
        else:
            return k

    def _sign_rtmp_url(self, url, bucket_name, channel_name, playlist_name, expires, params):
        expiration_time = int(time.time()) + expires

        canonicalized_resource = "/%s/%s" % (bucket_name, channel_name)
        canonicalized_params = []
        
        if params:
            items = params.items()
            for k,v in items:
                if k != "OSSAccessKeyId" and k != "Signature" and k!= "Expires" and k!= "SecurityToken":
                    canonicalized_params.append((k, v))
                    
        canonicalized_params.sort(key=lambda e: e[0]) 
        canon_params_str = ''
        for k, v in canonicalized_params:
            canon_params_str += '%s:%s\n' % (k, v)
        
        p = params if params else {}
        string_to_sign = str(expiration_time) + "\n" + canon_params_str + canonicalized_resource
        get_logger().debug('string_to_sign={0}'.format(string_to_sign))
        
        h = hmac.new(to_bytes(self.secret), to_bytes(string_to_sign), hashlib.sha1)
        signature = utils.b64encode_as_string(h.digest())

        p['OSSAccessKeyId'] = self.id
        p['Expires'] = str(expiration_time)
        p['Signature'] = signature

        return url + '?' + '&'.join(_param_to_quoted_query(k, v) for k, v in p.items())
    

class AnonymousAuth(object):
    """用于匿名访问。

    .. note::
        匿名用户只能读取public-read的Bucket，或只能读取、写入public-read-write的Bucket。
        不能进行Service、Bucket相关的操作，也不能罗列文件等。
    """
    def _sign_request(self, req, bucket_name, key):
        pass

    def _sign_url(self, req, bucket_name, key, expires):
        return req.url + '?' + '&'.join(_param_to_quoted_query(k, v) for k, v in req.params.items())
    
    def _sign_rtmp_url(self, url, bucket_name, channel_name, playlist_name, expires, params):
        return url + '?' + '&'.join(_param_to_quoted_query(k, v) for k, v in params.items())
        

class StsAuth(object):
    """用于STS临时凭证访问。可以通过官方STS客户端获得临时密钥（AccessKeyId、AccessKeySecret）以及临时安全令牌（SecurityToken）。

    注意到临时凭证会在一段时间后过期，在此之前需要重新获取临时凭证，并更新 :class:`Bucket <oss2.Bucket>` 的 `auth` 成员变量为新
    的 `StsAuth` 实例。

    :param str access_key_id: 临时AccessKeyId
    :param str access_key_secret: 临时AccessKeySecret
    :param str security_token: 临时安全令牌(SecurityToken)
    """
    def __init__(self, access_key_id, access_key_secret, security_token):
        self.__auth = Auth(access_key_id, access_key_secret)
        self.__security_token = security_token

    def _sign_request(self, req, bucket_name, key):
        req.headers['x-oss-security-token'] = self.__security_token
        self.__auth._sign_request(req, bucket_name, key)

    def _sign_url(self, req, bucket_name, key, expires):
        req.params['security-token'] = self.__security_token
        return self.__auth._sign_url(req, bucket_name, key, expires)
    
    def _sign_rtmp_url(self, url, bucket_name, channel_name, playlist_name, expires, params):
        params['security-token'] = self.__security_token
        return self.__auth._sign_rtmp_url(url, bucket_name, channel_name, playlist_name, expires, params)


def _param_to_quoted_query(k, v):
    if v:
        return urlquote(k, '') + '=' + urlquote(v, '')
    else:
        return urlquote(k, '')


def v2_uri_encode(raw_text):
    raw_text = to_bytes(raw_text)

    res = ''
    for b in raw_text:
        if isinstance(b, int):
            c = chr(b)
        else:
            c = b

        if (c >= 'A' and c <= 'Z') or (c >= 'a' and c <= 'z')\
            or (c >= '0' and c <= '9') or c in ['_', '-', '~', '.']:
            res += c
        else:
            res += "%{0:02X}".format(ord(c))

    return res


_DEFAULT_ADDITIONAL_HEADERS = set(['range',
                                   'if-modified-since'])


class AuthV2(object):
    def __init__(self, access_key_id, access_key_secret):
        #: AccessKeyId
        self.id = access_key_id.strip()

        #: AccessKeySecret
        self.secret = access_key_secret.strip()

    def _sign_request(self, req, bucket_name, key, in_additional_headers=None):
        """Insert Authorization header into a request.

        :param req: authorization information will be added into this request's Authorization HTTP header
        :type req: oss2.http.Request

        :param bucket_name: bucket name
        :param key: object key
        :param in_additional_headers: a list of additional header names to be included in signature calculation
        """
        if in_additional_headers is None:
            additional_headers = self.__get_additional_headers(req, _DEFAULT_ADDITIONAL_HEADERS)
        else:
            additional_headers = self.__get_additional_headers(req, in_additional_headers)

        req.headers['date'] = utils.http_date()

        signature = self.__make_signature(req, bucket_name, key, additional_headers)

        if additional_headers:
            req.headers['authorization'] = "OSS2 AccessKeyId:{0},AdditionalHeaders:{1},Signature:{2}"\
                .format(self.id, ';'.join(additional_headers), signature)
        else:
            req.headers['authorization'] = "OSS2 AccessKeyId:{0},Signature:{1}".format(self.id, signature)

    def _sign_url(self, req, bucket_name, key, expires, in_additional_headers=None):
        """Return a signed URL.

        :param req: the request to be signed
        :type req: oss2.http.Request

        :param bucket_name: bucket name
        :param key: object key
        :param int expires: the signed request will be timed out after `expires` seconds.
        :param in_additional_headers: a list of additional header names to be included in signature calculation

        :return: a signed URL
        """

        if in_additional_headers is None:
            additional_headers = {}
        else:
            additional_headers = self.__get_additional_headers(req, in_additional_headers)

        expiration_time = int(time.time()) + expires

        req.headers['date'] = str(expiration_time)  # re-use __make_signature by setting the 'date' header

        req.params['x-oss-signature-version'] = 'OSS2'
        req.params['x-oss-expires'] = str(expiration_time)
        req.params['x-oss-access-key-id'] = self.id

        signature = self.__make_signature(req, bucket_name, key, additional_headers)

        req.params['x-oss-signature'] = signature

        return req.url + '?' + '&'.join(_param_to_quoted_query(k, v) for k, v in req.params.items())

    def __make_signature(self, req, bucket_name, key, additional_headers):
        string_to_sign = self.__get_string_to_sign(req, bucket_name, key, additional_headers)

        logging.info('string_to_sign={0}'.format(string_to_sign))

        h = hmac.new(to_bytes(self.secret), to_bytes(string_to_sign), hashlib.sha256)
        return utils.b64encode_as_string(h.digest())

    def __get_additional_headers(self, req, in_additional_headers):
        # we add a header into additional_headers only if it is already in req's headers.

        additional_headers = set(h.lower() for h in in_additional_headers)
        keys_in_header = set(k.lower() for k in req.headers.keys())

        return additional_headers & keys_in_header

    def __get_string_to_sign(self, req, bucket_name, key, additional_header_list):
        verb = req.method
        content_md5 = req.headers.get('content-md5', '')
        content_type = req.headers.get('content-type', '')
        date = req.headers.get('date', '')

        canonicalized_oss_headers = self.__get_canonicalized_oss_headers(req, additional_header_list)
        additional_headers = ';'.join(sorted(additional_header_list))
        canonicalized_resource = self.__get_resource_string(req, bucket_name, key)

        return verb + '\n' +\
            content_md5 + '\n' +\
            content_type + '\n' +\
            date + '\n' +\
            canonicalized_oss_headers +\
            additional_headers + '\n' +\
            canonicalized_resource

    def __get_resource_string(self, req, bucket_name, key):
        if bucket_name:
            encoded_uri = v2_uri_encode('/' + bucket_name + '/' + key)
        else:
            encoded_uri = v2_uri_encode('/')

        logging.info('encoded_uri={0} key={1}'.format(encoded_uri, key))

        return encoded_uri + self.__get_canonalized_query_string(req)

    def __get_canonalized_query_string(self, req):
        encoded_params = {}
        for param, value in req.params.items():
            encoded_params[v2_uri_encode(param)] = v2_uri_encode(value)

        if not encoded_params:
            return ''

        sorted_params = sorted(encoded_params.items(), key=lambda e: e[0])
        return '?' + '&'.join(self.__param_to_query(k, v) for k, v in sorted_params)

    def __param_to_query(self, k, v):
        if v:
            return k + '=' + v
        else:
            return k

    def __get_canonicalized_oss_headers(self, req, additional_headers):
        """
        :param additional_headers: must be a list of headers in low case, and must not be 'x-oss-' prefixed.
        """
        canon_headers = []

        for k, v in req.headers.items():
            lower_key = k.lower()
            if lower_key.startswith('x-oss-') or lower_key in additional_headers:
                canon_headers.append((lower_key, v))

        canon_headers.sort(key=lambda x: x[0])

        return ''.join(v[0] + ':' + v[1] + '\n' for v in canon_headers)
