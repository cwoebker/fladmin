# -*- coding: utf-8 -*-

import redis
import datetime


class RedisMonitor(object):

    def __init__(self, servers):
        self.servers = servers

    def getStats(self, jsonOutput=None):
        response = []
        for server in self.servers:
            response.append(self.getStatsPerServer(server))

        if jsonOutput:
            new_response = []
            for item in response:
                for key, value in item.items():
                    new_key = item.get("addr") + "_" + key
                    new_response.append({new_key: value})

            return new_response

        return response

    def getStatsPerServer(self, url):

        try:
            connection = redis.from_url(url)
            info = connection.info()
            size = connection.dbsize()
            info.update({
                "status": "up",
                "server_name": url,
                "last_save_humanized": datetime.datetime.fromtimestamp(info.get("last_save_time")),
                "keys": int(size),
            })

            connection.connection_pool.disconnect()

        except redis.exceptions.ConnectionError:
            info = {
                "status": "down",
                "server_name": url,
                "connected_clients": 0,
                "used_memory_human": '?',
            }

        info.update({
            "addr": info.get("server_name")[0].replace(".", "-") + str(info.get("server_name")[1]),
        })

        screen_strategy = 'normal'
        if info.get("status") == 'down':
            screen_strategy = 'hidden'

        info.update({
            "screen_strategy": screen_strategy,
        })

        return info
