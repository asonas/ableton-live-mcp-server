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
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            track_names = result.get('data', [])
            if track_names:
                return f"Track Names: {', '.join(track_names)}"
        return "No tracks found"
    else:
        return f"Error getting track names: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def add_midi_notes(track_id: int, clip_id: int, pitch: int, start_time: float, duration: float, velocity: int = 127, mute: bool = False) -> str:
    """
    Add MIDI notes to a clip in Ableton Live.

    Args:
        track_id: Track index (0-based)
        clip_id: Clip index (0-based)
        pitch: MIDI note number (0-127)
        start_time: Start time in beats (float)
        duration: Note duration in beats (float)
        velocity: MIDI velocity (0-127, default 127)
        mute: Whether the note should be muted (default False)

    Returns:
        Success or error message
    """
    params = {
        "address": "/live/clip/add/notes",
        "args": [track_id, clip_id, pitch, start_time, duration, velocity, mute]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return f"Successfully added MIDI note: pitch={pitch}, start={start_time}, duration={duration}, velocity={velocity}"
    else:
        return f"Error adding MIDI note: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def remove_midi_notes(track_id: int, clip_id: int, pitch_start: Optional[int] = None, pitch_end: Optional[int] = None, time_start: Optional[float] = None, time_end: Optional[float] = None) -> str:
    """
    Remove MIDI notes from a clip in Ableton Live.

    Args:
        track_id: Track index (0-based)
        clip_id: Clip index (0-based)
        pitch_start: Starting MIDI note number for range (optional)
        pitch_end: Ending MIDI note number for range (optional)
        time_start: Starting time in beats for range (optional)
        time_end: Ending time in beats for range (optional)

    Returns:
        Success or error message
    """
    params = {
        "address": "/live/clip/remove/notes",
        "args": [track_id, clip_id]
    }

    # Add range parameters if provided
    if pitch_start is not None and pitch_end is not None:
        params["args"].extend([pitch_start, pitch_end])
        if time_start is not None and time_end is not None:
            params["args"].extend([time_start, time_end])

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        range_desc = ""
        if pitch_start is not None and pitch_end is not None:
            range_desc = f" (pitch range: {pitch_start}-{pitch_end})"
        if time_start is not None and time_end is not None:
            range_desc += f" (time range: {time_start}-{time_end})"
        return f"Successfully removed MIDI notes{range_desc}"
    else:
        return f"Error removing MIDI notes: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def create_midi_clip(track_id: int, length: float = 4.0) -> str:
    """
    Create a new MIDI clip in Ableton Live.

    Args:
        track_id: Track index (0-based)
        length: Clip length in beats (default 4.0)

    Returns:
        Success or error message
    """
    params = {
        "address": "/live/clip_slot/create_clip",
        "args": [track_id, 0, length]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return f"Successfully created MIDI clip on track {track_id} with length {length} beats"
    else:
        return f"Error creating MIDI clip: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def get_tempo() -> str:
    """
    Get the current tempo (BPM) of the Ableton Live song.

    Returns:
        The current tempo in BPM or error message
    """
    params = {
        "address": "/live/song/get/tempo",
        "args": []
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            tempo = result.get('data')
            if tempo is not None:
                return f"Current tempo: {tempo} BPM"
        return "Unable to get tempo"
    else:
        return f"Error getting tempo: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def set_tempo(tempo: float) -> str:
    """
    Set the tempo (BPM) of the Ableton Live song.

    Args:
        tempo: The tempo in BPM (20.0 - 999.0)

    Returns:
        Success or error message
    """
    # Validate tempo range
    if tempo < 20.0 or tempo > 999.0:
        return f"Error: Tempo must be between 20.0 and 999.0 BPM (got {tempo})"

    params = {
        "address": "/live/song/set/tempo",
        "args": [tempo]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return f"Successfully set tempo to {tempo} BPM"
    else:
        return f"Error setting tempo: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def set_track_name(track_id: int, name: str) -> str:
    """
    Set the name of a track in Ableton Live.

    Args:
        track_id: Track index (0-based)
        name: The new name for the track

    Returns:
        Success or error message
    """
    if not name.strip():
        return "Error: Track name cannot be empty"

    params = {
        "address": "/live/track/set/name",
        "args": [track_id, name]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return f"Successfully set track {track_id} name to '{name}'"
    else:
        return f"Error setting track name: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def create_drum_pattern(track_id: int, clip_id: int, pattern_type: str = "eight_beat", bars: int = 1) -> str:
    """
    Create a drum pattern in Ableton Live.

    Args:
        track_id: Track index (0-based)
        clip_id: Clip index (0-based)
        pattern_type: Pattern type ("eight_beat", "sixteen_beat", "basic_rock")
        bars: Number of bars (default 1)

    Returns:
        Success or error message
    """
    # Standard GM drum mapping
    kick = 36  # C1
    snare = 38  # D1
    hihat_closed = 42  # F#1
    hihat_open = 46  # A#1

    patterns = {
        "eight_beat": [
            # Kick on 1 and 3
            (kick, 0.0, 0.5, 127),
            (kick, 2.0, 0.5, 127),
            # Snare on 2 and 4
            (snare, 1.0, 0.5, 100),
            (snare, 3.0, 0.5, 100),
            # Hi-hat on all 8th notes
            (hihat_closed, 0.0, 0.25, 80),
            (hihat_closed, 0.5, 0.25, 60),
            (hihat_closed, 1.0, 0.25, 80),
            (hihat_closed, 1.5, 0.25, 60),
            (hihat_closed, 2.0, 0.25, 80),
            (hihat_closed, 2.5, 0.25, 60),
            (hihat_closed, 3.0, 0.25, 80),
            (hihat_closed, 3.5, 0.25, 60),
        ],
        "sixteen_beat": [
            # Kick
            (kick, 0.0, 0.25, 127),
            (kick, 2.5, 0.25, 100),
            # Snare
            (snare, 1.0, 0.25, 100),
            (snare, 3.0, 0.25, 100),
        ] + [(hihat_closed, i * 0.25, 0.125, 70 if i % 2 == 0 else 50) for i in range(16)],
        "basic_rock": [
            # Kick
            (kick, 0.0, 0.5, 127),
            (kick, 2.0, 0.5, 127),
            (kick, 2.75, 0.25, 100),
            # Snare
            (snare, 1.0, 0.5, 110),
            (snare, 3.0, 0.5, 110),
        ] + [(hihat_closed, i * 0.5, 0.25, 80) for i in range(8)]
    }

    if pattern_type not in patterns:
        return f"Unknown pattern type: {pattern_type}. Available: {', '.join(patterns.keys())}"

    pattern = patterns[pattern_type]

    added_notes = 0
    errors = []

    for bar in range(bars):
        bar_offset = bar * 4.0  # 4 beats per bar

        for pitch, start_time, duration, velocity in pattern:
            adjusted_start = start_time + bar_offset

            params = {
                "address": "/live/clip/add/notes",
                "args": [track_id, clip_id, pitch, adjusted_start, duration, velocity, False]
            }

            response = await ableton_client.send_rpc_request("send_message", params)
            if response['status'] == 'ok':
                added_notes += 1
            else:
                errors.append(f"Failed to add note at {adjusted_start}: {response.get('message', 'Unknown error')}")

    if errors:
        return f"Added {added_notes} notes with {len(errors)} errors: {'; '.join(errors[:3])}"
    else:
        return f"Successfully created {pattern_type} drum pattern: {added_notes} notes added across {bars} bar(s)"

if __name__ == "__main__":
    try:
        mcp.run()
    finally:
        asyncio.run(ableton_client.close())
