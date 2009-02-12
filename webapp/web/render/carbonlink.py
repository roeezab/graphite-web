"""Copyright 2008 Orbitz WorldWide

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License."""

import socket, cPickle
from django.conf import settings
from web.logger import log


class CarbonLinkPool:
  def __init__(self,hosts,timeout):
    self.hosts = hosts
    self.timeout = float(timeout)
    self.connections = {}
    # Create a connection pool for each host
    for host in hosts:
      self.connections[host] = set()

  def selectHost(self, metric):
    "Returns the carbon host that has data for the given metric"
    return self.hosts[ hash(metric) % len(self.hosts) ]

  def getConnection(self, host):
    # First try to take one out of the pool for this host
    connectionPool = self.connections[host]
    try:
      return connectionPool.pop()
    except KeyError:
      pass #nothing left in the pool, gotta make a new connection

    log.cache("CarbonLink creating a new socket for %s:%d" % host)
    connection = socket.socket()
    connection.settimeout(self.timeout)
    connection.connect(host)
    connection.setsockopt( socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1 )
    return connection

  def putConnectionInPool(self, host, connection):
    connectionPool = self.connections[host]
    connectionPool.add(connection)

  def removeConnectionFromPool(self, host, connection):
    connectionPool = self.connections.get(host, set())
    connectionPool.discard(connection)

  def sendRequest(self, metric):
    "Sends a request and returns a completion callback"
    host = self.selectHost(metric)
    query = metric + '\x00'
    connection = None

    try:
      connection = self.getConnection(host)
      connection.sendall(query)

      # To keep things asynchronous we return a result callback
      def receiveResponse():
        try:
          buf = ''
          while True:
            pkt = connection.recv(65536)
            assert pkt, "CarbonLink lost connection to %s:%d" % host
            buf += pkt
            if buf.endswith('\x00'): break

          # We're done with the connection for this request, put it in the pool
          self.putConnectionInPool(host, connection)

          # Now parse the response
          pointStrings = cPickle.loads(buf[:-1])
          log.cache("CarbonLink to %s, retrieved %d points for %s" % (host,len(pointStrings),metric))
          for point in pointStrings:
            (value, timestamp) = point.split(' ',1)
            yield ( int(timestamp), float(value) )
        except:
          log.exception("CarbonLink to %s, exception while getting response" % str(host))
          self.removeConnectionFromPool(host, connection)

      return receiveResponse
    except:
      log.exception("CarbonLink to %s, exception while sending request" % str(host))
      if connection:
        self.removeConnectionFromPool(host, connection)
      noResults = lambda: []
      return noResults


#parse hosts from local_settings.py
hosts = []
for host in settings.CARBONLINK_HOSTS:
  server,port = host.split(':',1)
  hosts.append( (server,int(port)) )

#create an importable singleton
CarbonLink = CarbonLinkPool(hosts, settings.CARBONLINK_TIMEOUT)
