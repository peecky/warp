#!/usr/bin/python
# -*- coding: utf-8 -*-

VERSION = "v0.1 (poc code)"

"""
Copyright (c) 2013 devunt

Permission is hereby granted, free of charge, to any person
obtaining a copy of this software and associated documentation
files (the "Software"), to deal in the Software without
restriction, including without limitation the rights to use,
copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following
conditions:

The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
OTHER DEALINGS IN THE SOFTWARE.
"""

from gevent.monkey import patch_all; patch_all()
from threading import Thread
from Queue import Queue
from socket import (AF_INET, IPPROTO_TCP, SO_REUSEADDR, SOCK_STREAM,
                    SOL_SOCKET, TCP_NODELAY, socket, error)
from re import compile
from time import sleep
from optparse import OptionParser
import logging


REGEX_HOST = compile(r'(^:+):([0-9]{1,5})')
REGEX_CONTENT_LENGTH = compile(r'\r\nContent-Length: ([0-9]+)\r\n')
REGEX_PROXY_CONNECTION = compile(r'\r\nProxy-Connection: (.+)\r\n')
REGEX_CONNECTION = compile(r'\r\nConnection: (.+)\r\n')
REGEX_USER_AGENTS_WITHOUT_PROXY_CONNECTION_HEADER = compile(r'\r\nUser-Agent: .*(Firefox|Opera).+\r\n')


class WorkerThread(Thread):
    def __init__(self, q, options):
        self.q = q
        self.nosleep = options['nosleep']
        Thread.__init__(self)

    def run(self):
        logging.debug('%s started' % self.name)
        while True:
            conn, addr = self.q.get(block=True)
            logging.debug('%s: Accept new task' % self.name)
            cont = ''
            try:
                while True:
                    data = conn.recv(1024)
                    if not data:
                        break
                    cont += data
                    if data.find('\r\n\r\n') != -1:
                        break
                m = REGEX_CONTENT_LENGTH.search(cont)
                if m:
                    cl = int(m.group(1))
                    ct = cont.split('\r\n\r\n')[1]
                    while (len(ct) < cl):
                        data = conn.recv(1024)
                        ct += data
                    cont = cont.split('\r\n\r\n')[0] + '\r\n\r\n' + ct
            except:
                pass

            m1 = REGEX_PROXY_CONNECTION.search(cont)
            m2 = REGEX_USER_AGENTS_WITHOUT_PROXY_CONNECTION_HEADER.search(cont)
            if not m1 and not m2:
                self.q.task_done()
                logging.debug('!!! %s: Task reject' % self.name)
                return

            req = cont.split('\r\n')
            if len(req) < 4:
                self.q.task_done()
                logging.debug('!!! %s: Task reject' % self.name)
                return
            head = req[0].split(' ')
            phost = False
            sreq = []
            sreqHeaderEndIndex = 0
            for line in req[1:]:
                if "Host: " in line:
                    phost = line[6:]
                elif not 'Proxy-Connection' in line:
                    sreq.append(line)
                    if len(line) == 0 and sreqHeaderEndIndex == 0:
                        sreqHeaderEndIndex = len(sreq) - 1
            if sreqHeaderEndIndex == 0:
                sreqHeaderEndIndex = len(sreq)

            m = REGEX_CONNECTION.search(cont)
            if not m:
                sreq.insert(sreqHeaderEndIndex, "Connection: close")

            if not phost:
                phost = '127.0.0.1'
            path = head[1][len(phost)+7:]

            logging.debug('%s: Process - %s' % (self.name, req[0]))

            new_head = ' '.join([head[0], path, head[2]])

            m = REGEX_HOST.search(phost)
            if m:
                host = m.group(1)
                port = int(m.group(2))
            else:
                host = phost
                port = 80
                phost = "%s:80" % host

            try:
                req_sc = socket(AF_INET, SOCK_STREAM)
                #req_sc.setsockopt(IPPROTO_TCP, TCP_NODELAY, 1)
                req_sc.connect((host, port))
                req_sc.send('%s\r\n' % new_head)

                if not self.nosleep:
                    sleep(0.2)

                req_sc.setsockopt(IPPROTO_TCP, TCP_NODELAY, 1)
                req_sc.send('Host: ')
                def feed_phost(phost):
                    import random
                    i = 1
                    while phost:
                        yield random.randrange(2, 4), phost[:i]
                        phost = phost[i:]
                        i = random.randrange(2, 5)
                for delay, c in feed_phost(phost):
                    if not self.nosleep:
                        sleep(delay/10.0)
                    req_sc.send(c)
                req_sc.setsockopt(IPPROTO_TCP, TCP_NODELAY, 0)
                req_sc.sendall('\r\n' + '\r\n'.join(sreq))

            except:
                pass

            while True:
                try:
                    buf = req_sc.recv(1024)
                    if len(buf) == 0:
                        break
                    conn.send(buf)
                except:
                    pass

            req_sc.close()
            conn.close()

            logging.debug('%s: Task done' % self.name)
            self.q.task_done()


class Server(object):
    def __init__(self, hostname, port, count, nosleep):
        self.hostname = hostname
        self.port = port
        self.count = count
        self.nosleep = nosleep
        self.q = Queue()

    def start(self):
        for i in range(0, self.count):
            th = WorkerThread(self.q, {'nosleep': self.nosleep})
            th.name = 'Worker #%d' % (i + 1)
            th.daemon = True
            th.start()
        self.sc = socket(AF_INET, SOCK_STREAM)
        try:
            self.sc.bind((self.hostname, self.port))
        except error as e:
            logging.critical('!!! Fail to bind server at [%s:%d]: %s' % (self.hostname, self.port, e.args[1]))
            return 1
        logging.info('Server bound at [%s:%d]. Listen with %d threads.' % (self.hostname, self.port, self.count))
        self.sc.listen(10)

        while True:
            self.q.put(self.sc.accept())


def main():
    """CLI frontend function.  It takes command line options e.g. host,
    port and provides ``--help`` message.

    """
    parser = OptionParser(description='Simple HTTP transparent proxy',
                          version=VERSION)
    parser.add_option('-H', '--host', default='127.0.0.1',
                      help='Host to listen [%default]')
    parser.add_option('-p', '--port', type='int', default=8800,
                      help='Port to listen [%default]')
    parser.add_option('-c', '--count', type='int', default=64,
                      help='Count of thread to spawn [%default]')
    parser.add_option('--nosleep', action="store_true",
                      help='No time delay during send HTTP Host')
    parser.add_option('-v', '--verbose', action="store_true",
                      help='Print verbose')
    options, args = parser.parse_args()
    if not (1 <= options.port <= 65535):
        parser.error('port must be 1-65535')
    if options.verbose:
        lv = logging.DEBUG
    else:
        lv = logging.INFO
    logging.basicConfig(level=lv, format='[%(asctime)s] {%(levelname)s} %(message)s')
    server = Server(options.host, options.port, options.count, options.nosleep)
    try:
        return server.start()
    except KeyboardInterrupt:
        print 'bye'


if __name__ == '__main__':
    exit(main())
