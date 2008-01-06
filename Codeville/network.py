# Written by Ross Cohen
# see LICENSE.txt for license information

import binascii
import socket
import struct

Request = 0
Response = 1

class NetworkError(StandardError):
    pass

class NetworkHandler:
    def __init__(self, handler):
        self.socket = {}
        self.hfmt = "!I20s"
        self.rrfmt = "!BxxxI"
        self.hlen = struct.calcsize(self.hfmt)
        self.rrlen = struct.calcsize(self.rrfmt)
        self.handler = handler
        self.handler.nh = self
        self.rs = handler.rs

    def external_connection_made(self, s):
        self.socket[s] = {'message': [],
                          'len': self.hlen,
                          'hread': 0,
                          'mid': 0,
                          'nextid': 0,
                          'mlen': 0,
                          'skip': 0,
                          'req-mode': 0}
        self.handler.external_connection_made(s)

    def start_connection(self, dns):
        try:
            s = self.rs.start_connection(dns, handler = self)
        except socket.error, e:
            raise NetworkError, e[1]
        self.socket[s] = {'message': [],
                          'len': self.hlen,
                          'hread': 0,
                          'mid': 0,
                          'nextid': 0,
                          'mlen': 0,
                          'skip': 0,
                          'req-mode': 0}
        return s

    def _read_sock(self, socket, length):
        count, datas = 0, []
        skip = socket['skip']
        while count < length:
            data = socket['message'][0]
            dlength = len(data)
            count += dlength - skip
            if count > length:
                data = data[skip:dlength-(count-length)]
                skip = dlength-(count-length)
            else:
                del socket['message'][0]
                if skip != 0:
                    data = data[skip:]
                skip = 0
            datas.append(data)
        socket['mlen'] -= length
        socket['skip'] = skip
        retval = ''.join(datas)
        assert len(retval) == length
        return retval

    def _write_sock(self, socket, data):
        socket['mlen'] += len(data)
        socket['message'].append(data)

    def data_came_in(self, s, data):
        socket = self.socket[s]
        self._write_sock(socket, data)
        while socket['mlen'] >= socket['len']:
            if socket['hread'] == 0:
                header = self._read_sock(socket, self.hlen)
                socket['len'], socket['mac'] = struct.unpack(self.hfmt, header)
                socket['hread'] = 1
            else:
                if socket['req-mode']:
                    header = self._read_sock(socket, self.rrlen)
                    if socket.has_key('h_in'):
                        socket['h_in'].update(header)
                    data = self._read_sock(socket, socket['len'] - self.rrlen)
                else:
                    data = self._read_sock(socket, socket['len'])

                if socket.has_key('h_in'):
                    socket['h_in'].update(data)
                    if socket['mac'] != socket['h_in'].digest():
                        s.close()
                        print binascii.hexlify(socket['mac'])
                        print binascii.hexlify(socket['h_in'].digest())
                        self.connection_lost(s, 'Bad HMAC')

                if socket['req-mode']:
                    (rr, mid) = struct.unpack(self.rrfmt, header)
                    if rr == Request:
                        self.handler.request_came_in(s, mid, data)
                    elif rr == Response:
                        self.handler.response_came_in(s, mid, data)
                else:
                    self.handler.message_came_in(s, data)
                socket['len'] = self.hlen
                socket['hread'] = 0

    def connection_lost(self, s, msg):
        self.handler.connection_lost(s, msg)
        del self.socket[s]

    def connection_flushed(self, s):
        if self.socket[s]['req-mode']:
            self.handler.connection_flushed(s)

    def _send_msg(self, s, data):
        socket = self.socket[s]
        if socket.has_key('h_out'):
            socket['h_out'].update(data)
            mac = socket['h_out'].digest()
        else:
            mac = '\x00' * 20
        return s.write(struct.pack(self.hfmt, len(data), mac) + data)

    # functions to be called by higher layer
    def set_hmac(self, s, h_in, h_out):
        self.socket[s]['h_in'] = h_in
        self.socket[s]['h_out'] = h_out

    def get_req_mode(self, s):
        return self.socket[s]['req-mode']

    def req_mode(self, s, mode):
        self.socket[s]['req-mode'] = mode

    def send_msg(self, s, data):
        socket = self.socket[s]
        if socket['req-mode']:
            raise NetworkError, 'send_msg called in wrong mode'
        return self._send_msg(s, data)

    def send_request(self, s, data):
        socket = self.socket[s]
        if not socket['req-mode']:
            raise NetworkError, 'send_request called in wrong mode'
        socket['nextid'] += 1
        self._send_msg(s, struct.pack(self.rrfmt, Request, socket['nextid']) + data)
        return socket['nextid']

    def send_response(self, s, mid, data):
        socket = self.socket[s]
        if not socket['req-mode']:
            raise NetworkError, 'send_response called in wrong mode'
        return self._send_msg(s, struct.pack(self.rrfmt, Response, mid) + data)

    def next_id(self, s):
        socket = self.socket[s]
        socket['nextid'] += 1
        return socket['nextid']

    def close(self, s):
        s.close()
        self.socket[s]['mlen'] = 0
        del self.socket[s]
