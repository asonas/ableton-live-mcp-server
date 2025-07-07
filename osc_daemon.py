# osc_daemon.py
import asyncio
from pythonosc.udp_client import SimpleUDPClient
from pythonosc.osc_server import AsyncIOOSCUDPServer
from pythonosc.dispatcher import Dispatcher
import json
from typing import Optional, Dict, Any

class AbletonOSCDaemon:
    def __init__(self,
                 socket_host='127.0.0.1', socket_port=65432,
                 ableton_host='127.0.0.1', ableton_port=11000,
                 receive_port=11001):
        self.socket_host = socket_host
        self.socket_port = socket_port
        self.ableton_host = ableton_host
        self.ableton_port = ableton_port
        self.receive_port = receive_port

        # Initialize OSC client for Ableton
        self.osc_client = SimpleUDPClient(ableton_host, ableton_port)

        # Store active connections waiting for responses
        self.pending_responses: Dict[str, asyncio.Future] = {}

        # Initialize OSC server dispatcher
        self.dispatcher = Dispatcher()
        self.dispatcher.set_default_handler(self.handle_ableton_message)

    def handle_ableton_message(self, address: str, *args):
        """Handle incoming OSC messages from Ableton."""
        print(f"[ABLETON MESSAGE] Address: {address}, Args: {args}")

        # If this address has a pending response, resolve it
        if address in self.pending_responses:
            future = self.pending_responses[address]
            if not future.done():
                future.set_result({
                    'status': 'success',
                    'address': address,
                    'data': args
                })
            del self.pending_responses[address]

    async def start(self):
        """Start both the socket server and OSC server."""
        # Start OSC server to receive Ableton messages
        self.osc_server = AsyncIOOSCUDPServer(
            (self.socket_host, self.receive_port),
            self.dispatcher,
            asyncio.get_event_loop()
        )
        await self.osc_server.create_serve_endpoint()

        # Start socket server for MCP communication
        server = await asyncio.start_server(
            self.handle_socket_client,
            self.socket_host,
            self.socket_port
        )
        print(f"Ableton OSC Daemon listening on {self.socket_host}:{self.socket_port}")
        print(f"OSC Server receiving on {self.socket_host}:{self.receive_port}")
        print(f"Sending to Ableton on {self.ableton_host}:{self.ableton_port}")

        async with server:
            await server.serve_forever()

    async def handle_socket_client(self, reader, writer):
        """Handle incoming socket connections from MCP server."""
        client_address = writer.get_extra_info('peername')
        print(f"[NEW CONNECTION] Client connected from {client_address}")

        try:
            while True:
                data = await reader.read(1024)
                if not data:
                    break

                try:
                    message = json.loads(data.decode())
                    print(f"[RECEIVED MESSAGE] From {client_address}: {message}")

                    if 'method' in message:
                        command = message.get('method')
                        params = message.get('params', {})
                        address = params.get('address')  # params の中から address を取得
                        args = params.get('args', [])     # params の中から args を取得
                    else:
                        # 既存のフォーマット
                        command = message.get('command')
                        address = message.get('address')
                        args = message.get('args', [])


                    if command == 'send_message':
                        # For commands that expect responses, set up a future
                        if address.startswith(('/live/device/get', '/live/scene/get', '/live/view/get', '/live/clip/get', '/live/clip_slot/get', '/live/track/get', '/live/song/get', '/live/api/get', '/live/application/get', '/live/test', '/live/error')):
                            # Create response future with timeout
                            future = asyncio.Future()
                            self.pending_responses[address] = future

                            # Send to Ableton
                            self.osc_client.send_message(address, args)

                            try:
                                # Wait for response with timeout
                                response = await asyncio.wait_for(future, timeout=5.0)
                                print(f"[OSC RESPONSE] Received: {response}")
                                # JSONRPCフォーマットでラップ
                                jsonrpc_response = {
                                    "jsonrpc": "2.0",
                                    "id": message.get('id'),
                                    "result": response
                                }
                                writer.write(json.dumps(jsonrpc_response).encode())
                            except asyncio.TimeoutError:
                                response = {
                                    'status': 'error',
                                    'message': f'Timeout waiting for response to {address}'
                                }
                                print(f"[OSC TIMEOUT] {response}")
                                # JSONRPCフォーマットでラップ
                                jsonrpc_response = {
                                    "jsonrpc": "2.0",
                                    "id": message.get('id'),
                                    "error": {
                                        "code": -32603,
                                        "message": f'Timeout waiting for response to {address}'
                                    }
                                }
                                writer.write(json.dumps(jsonrpc_response).encode())

                        else:
                            # For commands that don't expect responses
                            self.osc_client.send_message(address, args)
                            jsonrpc_response = {
                                "jsonrpc": "2.0",
                                "id": message.get('id'),
                                "result": {'status': 'sent'}
                            }
                            writer.write(json.dumps(jsonrpc_response).encode())

                    elif command == 'get_status':
                        response = {
                            'status': 'ok',
                            'ableton_port': self.ableton_port,
                            'receive_port': self.receive_port
                        }
                        print(f"[STATUS REQUEST] Responding with: {response}")
                        # JSONRPCフォーマットでラップ
                        jsonrpc_response = {
                            "jsonrpc": "2.0",
                            "id": message.get('id'),
                            "result": response
                        }
                        writer.write(json.dumps(jsonrpc_response).encode())
                    else:
                        print(f"[UNKNOWN COMMAND] Received: {message}")
                        jsonrpc_response = {
                            "jsonrpc": "2.0",
                            "id": message.get('id'),
                            "error": {
                                "code": -32601,
                                "message": "Unknown command"
                            }
                        }
                        writer.write(json.dumps(jsonrpc_response).encode())

                    await writer.drain()

                except json.JSONDecodeError:
                    print(f"[JSON ERROR] Could not decode message: {data}")
                    response = {'status': 'error', 'message': 'Invalid JSON'}
                    writer.write(json.dumps(response).encode())

        except Exception as e:
            print(f"[CONNECTION ERROR] Error handling client: {e}")
        finally:
            writer.close()
            await writer.wait_closed()
            print(f"[CONNECTION CLOSED] Client {client_address} disconnected")

if __name__ == "__main__":
    daemon = AbletonOSCDaemon()
    asyncio.run(daemon.start())
