from mcbbs.transports.base import Transport


class FakeTransport(Transport):
    """Atrapa transportu: zapisuje, co BBS chcial wyslac."""

    def __init__(self):
        super().__init__()
        self.sent = []      # (dst, text, key)
        self.acks = []      # (dst, msgno)
        self._n = 0
        self.up = True

    @property
    def connected(self):
        return self.up

    def start(self):
        pass

    def send(self, dst, text):
        self._n += 1
        self.sent.append((dst, text, self._n))
        return self._n

    def send_ack(self, dst, msgno):
        self.acks.append((dst, msgno))
