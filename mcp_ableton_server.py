from mcp.server.fastmcp import FastMCP
import asyncio
import json
import socket
import sys
from typing import List, Optional

class AbletonClient:
    def __init__(self, host='127.0.0.1', port=65432):
        self.host = host
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.connected = False
        self.responses = {}  # Store futures keyed by (request_id)
        self.lock = asyncio.Lock()
        self._request_id = 0  # compteur pour générer des ids uniques

        # Task asynchrone pour lire les réponses
        self.response_task = None

    async def start_response_reader(self):
        """Background task to read responses from the socket, potentially multiple messages."""
        # On convertit self.sock en Streams asyncio
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        loop = asyncio.get_running_loop()
        await loop.create_connection(lambda: protocol, sock=self.sock)

        while self.connected:
            try:
                data = await reader.read(4096)
                if not data:
                    # Connection close
                    break

                try:
                    msg = json.loads(data.decode())
                except json.JSONDecodeError:
                    print("Invalid JSON from daemon", file=sys.stderr)
                    continue

                # Si c'est une réponse JSON-RPC
                resp_id = msg.get('id')
                if 'result' in msg or 'error' in msg:
                    # Réponse à une requête
                    async with self.lock:
                        fut = self.responses.pop(resp_id, None)
                    if fut and not fut.done():
                        fut.set_result(msg)
                else:
                    # Sinon c'est un message "osc_response" ou un autre type
                    # (Selon le code du daemon)
                    if msg.get('type') == 'osc_response':
                        # On peut router selon l'adresse
                        address = msg.get('address')
                        args = msg.get('args')
                        await self.handle_osc_response(address, args)
                    else:
                        print(f"Unknown message: {msg}", file=sys.stderr)

            except Exception as e:
                print(f"Error reading response: {e}", file=sys.stderr)
                break

    async def handle_osc_response(self, address: str, args):
        """Callback quand on reçoit un message de type OSC depuis Ableton."""
        # Exemple simple : on pourrait faire un set_result sur un future
        print(f"OSC Notification from {address}: {args}", file=sys.stderr)

    def connect(self):
        """Connect to the OSC daemon via TCP socket."""
        if not self.connected:
            try:
                self.sock.connect((self.host, self.port))
                self.connected = True

                # Start the response reader task
                self.response_task = asyncio.create_task(self.start_response_reader())
                return True
            except Exception as e:
                print(f"Failed to connect to daemon: {e}", file=sys.stderr)
                return False
        return True

    async def send_rpc_request(self, method: str, params: dict) -> dict:
        """
        Envoie une requête JSON-RPC (method, params) et attend la réponse.
        """
        if not self.connected:
            if not self.connect():
                return {'status': 'error', 'message': 'Not connected to daemon'}

        # Génération d'un ID unique
        self._request_id += 1
        request_id = str(self._request_id)

        # Construit la requête JSON-RPC
        request_obj = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params
        }

        future = asyncio.Future()
        async with self.lock:
            self.responses[request_id] = future

        try:
            self.sock.sendall(json.dumps(request_obj).encode())

            # Attend la réponse JSON-RPC
            try:
                msg = await asyncio.wait_for(future, timeout=5.0)
            except asyncio.TimeoutError:
                async with self.lock:
                    self.responses.pop(request_id, None)
                return {'status': 'error', 'message': 'Response timeout'}

            # On check si on a un 'result' ou un 'error'
            if 'error' in msg:
                return {
                    'status': 'error',
                    'code': msg['error'].get('code'),
                    'message': msg['error'].get('message')
                }
            else:
                return {
                    'status': 'ok',
                    'result': msg.get('result')
                }

        except Exception as e:
            self.connected = False
            return {'status': 'error', 'message': str(e)}
    """
    def send_rpc_command_sync(self, method: str, params: dict) -> dict:

        # Variante synchrone pour juste envoyer le message
        # et lire UNE réponse immédiatement (fonctionne si
        # le daemon renvoie une unique réponse).

        if not self.connected:
            if not self.connect():
                return {'status': 'error', 'message': 'Not connected'}

        # On envoie un ID, etc.
        self._request_id += 1
        request_id = str(self._request_id)

        request_obj = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params
        }
        try:
            self.sock.sendall(json.dumps(request_obj).encode())
            resp_data = self.sock.recv(4096)
            if not resp_data:
                return {'status': 'error', 'message': 'No response'}

            msg = json.loads(resp_data.decode())
            if 'error' in msg:
                return {
                    'status': 'error',
                    'code': msg['error'].get('code'),
                    'message': msg['error'].get('message')
                }
            else:
                return {'status': 'ok', 'result': msg.get('result')}

        except Exception as e:
            self.connected = False
            return {'status': 'error', 'message': str(e)}
    """
    async def close(self):
        """Close the connection."""
        if self.connected:
            self.connected = False
            if self.response_task:
                self.response_task.cancel()
                try:
                    await self.response_task
                except asyncio.CancelledError:
                    pass
            self.sock.close()


# Initialize the MCP server
mcp = FastMCP("Ableton Live Controller", dependencies=["python-osc"])

# Create Ableton client
ableton_client = AbletonClient()


# ----- TOOLS WITH RESPONSE -----

@mcp.tool()
async def get_track_names(index_min: Optional[int] = None, index_max: Optional[int] = None) -> str:
    """
    Get the names of tracks in Ableton Live.

    Args:
        index_min: Optional minimum track index
        index_max: Optional maximum track index

    Returns:
        A formatted string containing track names
    """
    params = {}
    if index_min is not None and index_max is not None:
        params["address"] = "/live/song/get/track_names"
        params["args"] = [index_min, index_max]
    else:
        params["address"] = "/live/song/get/track_names"
        params["args"] = []

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        track_names = response['result'].get('status')
        # Ici, j'ai mis 'status' car dans le daemon, on renvoie "result": {"status":"sent"} ou autre
        # Mais si vous modifiez le daemon pour retourner vraiment les noms de pistes, changez la structure correspondante.
        if not track_names:
            return "No tracks found"
        # Supposons qu'on reçoive un tableau de noms => adapter en conséquence
        # track_names = ["Track1", "Track2", ...]
        # ...
        return f"Track Names: {track_names}"
    else:
        return f"Error getting track names: {response.get('message', 'Unknown error')}"

if __name__ == "__main__":
    try:
        mcp.run()
    finally:
        asyncio.run(ableton_client.close())
