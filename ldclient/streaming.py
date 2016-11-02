from __future__ import absolute_import

import json
from threading import Thread

import time

from requests import HTTPError
from sseclient import SSEClient
from ldclient.interfaces import UpdateProcessor
from ldclient.util import _stream_headers, log


class StreamingUpdateProcessor(Thread, UpdateProcessor):
    def __init__(self, sdk_key, config, requester, store, ready):
        Thread.__init__(self)
        self.daemon = True
        self._sdk_key = sdk_key
        self._config = config
        self._requester = requester
        self._store = store
        self._running = False
        self._ready = ready

    def run(self):
        log.info("Starting StreamingUpdateProcessor connecting to uri: " + self._config.stream_uri)
        self._running = True
        hdrs = _stream_headers(self._sdk_key)
        uri = self._config.stream_uri
        while self._running:
            try:
                messages = SSEClient(uri, verify=self._config.verify_ssl, headers=hdrs)
                for msg in messages:
                    if not self._running:
                        break
                    message_ok = self.process_message(self._store, self._requester, msg, self._ready)
                    if message_ok is True and self._ready.is_set() is False:
                        self._ready.set()
            except HTTPError as e:
                if e.response is not None and e.response.status_code is not None:
                    if 400 <= e.response.status_code < 500:
                        log.error("StreamingUpdateProcessor response: " + str(e) + ". Retries will not be attempted.")
                        if self._ready.is_set() is False:
                            self._ready.set()
                        self._running = False
                        return
            except Exception as e:
                log.error("Could not connect to LaunchDarkly stream: " + str(e.message) +
                          " waiting 1 second before trying again.")
                time.sleep(1)

    def stop(self):
        log.info("Stopping StreamingUpdateProcessor")
        self._running = False

    def initialized(self):
        return self._running and self._ready.is_set() is True and self._store.initialized is True

    @staticmethod
    def process_message(store, requester, msg, ready):
        log.debug("Received stream event {} with data: {}".format(msg.event, msg.data))
        if msg.event == 'put':
            payload = json.loads(msg.data)
            store.init(payload)
            if not ready.is_set() is True and store.initialized is True:
                log.info("StreamingUpdateProcessor initialized ok")
                return True
        elif msg.event == 'patch':
            payload = json.loads(msg.data)
            key = payload['path'][1:]
            feature = payload['data']
            store.upsert(key, feature)
        elif msg.event == "indirect/patch":
            key = msg.data
            store.upsert(key, requester.get_one(key))
        elif msg.event == "indirect/put":
            store.init(requester.get_all())
            if not ready.is_set() is True and store.initialized is True:
                log.info("StreamingUpdateProcessor initialized ok")
                return True
        elif msg.event == 'delete':
            payload = json.loads(msg.data)
            key = payload['path'][1:]
            # noinspection PyShadowingNames
            version = payload['version']
            store.delete(key, version)
        else:
            log.warning('Unhandled event in stream processor: ' + msg.event)
        return False
