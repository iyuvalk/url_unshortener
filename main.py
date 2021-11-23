import base64
import os
import sys
import socket
import json
import threading
import time
import uuid
from threading import Thread
import collections
import argparse
import hashlib
import requests
from urllib.parse import urlparse


class SimpleLRUCache:
    def __init__(self, size):
        self.size = size
        self.id = uuid.uuid4()
        self.cache_lock = threading.Lock()
        self._lru_cache = collections.OrderedDict()

    def hash_value(self, value):
        return hashlib.md5(value.encode()).hexdigest()

    def get(self, key):
        # print("DEBUG: Retrieving item (" + key + ") from cache (" + str(self.id) + ")...")
        value_hash = hashlib.md5(key)
        with self.cache_lock:
            try:
                value = self._lru_cache.pop(key)
                self._lru_cache[key] = value
                return str(value)
            except KeyError:
                return None

    def __put(self, key, value):
        try:
            self._lru_cache.pop(key)
        except KeyError:
            if len(self._lru_cache) >= self.size:
                self._lru_cache.popitem(last=False)
        self._lru_cache[key] = value

    def put(self, key, value):
        # print("DEBUG: Appending item (" + value + ") to cache (" + str(self.id) + ")...")
        with self.cache_lock:
            self.__put(key, value)

    def put_if_not_exist(self, key, value):
        # print("DEBUG: Appending item (" + value + ") to cache (" + str(self.id) + ") if it does not exist...")
        key_hash = self.hash_value(key)
        with self.cache_lock:
            if key not in self._lru_cache:
                self.__put(key, value)

    def exists(self, key):
        print("DEBUG: Checking if item (" + key + ") exists in cache (" + str(self.id) + ")...")
        with self.cache_lock:
            return key in self._lru_cache

    def dump(self):
        # print("DEBUG: Dumping items from cache (" + str(self.id) + ")...")
        with self.cache_lock:
            return self._lru_cache.copy().items()

    def len(self):
        # print("DEBUG: Getting length of cache (" + str(self.id) + ")...")
        with self.cache_lock:
            return len(self._lru_cache)


class UrlUnshortener:
    def __init__(self, socket_path, max_cache_size, max_timeout=1):
        self.socket_path = socket_path
        self.max_timeout = max_timeout
        self.cache = SimpleLRUCache(max_cache_size)
        self.threads_count = 0
        self.threads_lock = threading.Lock()

    def unshorten(self, url):
        cached_value = self.cache.get(url.encode())
        if cached_value:
            return {"unshorten_info": cached_value, "is_cached": True}
        else:
            try:
                response = requests.head(url, timeout=self.max_timeout)
                if 3 <= response.status_code / 100 < 4 and 'Location' in response.headers.keys():
                    result_url = response.headers.get('Location')
                    parsed_source_url = urlparse(url)
                    parsed_result_url = urlparse(result_url)
                    both_on_same_host = parsed_result_url.hostname == parsed_source_url.hostname
                    result = {
                        "redirects_to": result_url,
                        "redirected_to_same_host": both_on_same_host
                    }
                    self.cache.put(url.encode(), result)
                    return {"unshorten_info": result, "is_cached": False}
                else:
                    return {"unshorten_info": {}, "is_cached": False}
            except Exception as exUnshorten:
                print("Exception occurred in unshorten: " + str(exUnshorten))
                return {"unshorten_info": {}, "is_cached": False}

    def start(self):
        # Make sure the socket does not already exist
        try:
            os.unlink(self.socket_path)
        except OSError:
            if os.path.exists(self.socket_path):
                raise

        # Create a UDS socket
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

        # Bind the socket to the address
        print('starting up on {}'.format(self.socket_path))
        sock.bind(self.socket_path)

        # Listen for incoming connections
        sock.listen(1)

        while True:
            # Wait for a connection
            connection, client_address = sock.accept()
            Thread(target=self.connection_handler, args=[connection]).start()

    def connection_handler(self, conn):
        with self.threads_lock:
            self.threads_count = self.threads_count + 1
        try:
            raw_data = conn.recv(1024)
            raw_data_string = raw_data.decode()
            command_obj = json.loads(raw_data.decode())
            if "text" not in command_obj:
                print("ERROR: Command misunderstood. Command: " + base64.b64decode(raw_data_string).decode())
                conn.sendall("ERROR: Command misunderstood".encode())
            else:
                time_started = time.time()
                result = self.unshorten(command_obj["text"])
                time_taken = time.time() - time_started
                result["time_taken"] = time_taken
                conn.sendall(json.dumps(result).encode())
        except Exception as exConnHandler:
            try:
                print("Exception occurred in connection_handler: " + str(exConnHandler))
                conn.sendall(json.dumps({}).encode())
            except:
                pass
        finally:
            try:
                conn.close()
            except:
                pass


if __name__ == "__main__":
    args_parser = argparse.ArgumentParser(description="Service for detecting similarity between texts")
    args_parser.add_argument("--socket-file", required=True,
                             help="The socket file path on which the service will listen", type=str)
    args_parser.add_argument("--max-timeout", required=False, help="Maximal amount of time in seconds to wait for the HEAD request to return results", type=int, default=1)
    args_parser.add_argument("--max-cache-size", required=True, help="Maximum number of entries to hold in the cache",
                             type=int)

    args = args_parser.parse_args()
    socket_path = os.path.realpath(args.socket_file)

    print("Starting with default parameters... (socket: " + socket_path + ")")
    if os.path.realpath(sys.argv[0]) == socket_path:
        print("ERROR: The socket path refers to the script itself. Something is wrong in the arguments list.")
        args_parser.print_help()
        exit(8)

    while True:
        try:
            server = UrlUnshortener(socket_path=socket_path, max_timeout=args.max_timeout,
                                   max_cache_size=args.max_cache_size)
            server.start()
        except KeyboardInterrupt:
            break
