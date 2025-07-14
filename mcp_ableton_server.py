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
async def create_midi_clip(track_id: int, clip_id: int = 0, length: float = 4.0, replace_existing: bool = False) -> str:
    """
    Create a new MIDI clip in Ableton Live.

    Args:
        track_id: Track index (0-based)
        clip_id: Clip index (0-based, default 0)
        length: Clip length in beats (default 4.0)
        replace_existing: If True, delete existing clip before creating new one

    Returns:
        Success or error message
    """
    # Check if clip already exists
    check_params = {
        "address": "/live/clip_slot/get/has_clip",
        "args": [track_id, clip_id]
    }

    check_response = await ableton_client.send_rpc_request("send_message", check_params)
    if check_response['status'] == 'ok':
        result = check_response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            has_clip_data = result.get('data')
            if has_clip_data:
                if replace_existing:
                    # Delete existing clip first
                    delete_params = {
                        "address": "/live/clip_slot/delete_clip",
                        "args": [track_id, clip_id]
                    }
                    delete_response = await ableton_client.send_rpc_request("send_message", delete_params)
                    if delete_response['status'] != 'ok':
                        return f"Error deleting existing clip: {delete_response.get('message', 'Unknown error')}"
                else:
                    return f"Clip already exists at track {track_id}, clip slot {clip_id}. Use replace_existing=True to overwrite."

    # Create new clip
    params = {
        "address": "/live/clip_slot/create_clip",
        "args": [track_id, clip_id, length]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return f"Successfully created MIDI clip on track {track_id}, clip slot {clip_id} with length {length} beats"
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
async def has_clip(track_id: int, clip_id: int) -> str:
    """
    Check if a clip slot has a clip in Ableton Live.

    Args:
        track_id: Track index (0-based)
        clip_id: Clip index (0-based)

    Returns:
        Whether the clip slot has a clip or error message
    """
    params = {
        "address": "/live/clip_slot/get/has_clip",
        "args": [track_id, clip_id]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            has_clip_data = result.get('data')
            if has_clip_data is not None:
                return f"Track {track_id}, Clip {clip_id} has clip: {has_clip_data}"
        return f"Unable to check clip existence for track {track_id}, clip {clip_id}"
    else:
        return f"Error checking clip existence: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def delete_clip(track_id: int, clip_id: int) -> str:
    """
    Delete a clip from a clip slot in Ableton Live.

    Args:
        track_id: Track index (0-based)
        clip_id: Clip index (0-based)

    Returns:
        Success or error message
    """
    params = {
        "address": "/live/clip_slot/delete_clip",
        "args": [track_id, clip_id]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return f"Successfully deleted clip from track {track_id}, clip slot {clip_id}"
    else:
        return f"Error deleting clip: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def get_clip_notes(track_id: int, clip_id: int) -> str:
    """
    Get all MIDI notes from a clip in Ableton Live.

    Args:
        track_id: Track index (0-based)
        clip_id: Clip index (0-based)

    Returns:
        A formatted string containing all MIDI notes or error message
    """
    params = {
        "address": "/live/clip/get/notes",
        "args": [track_id, clip_id]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            notes_data = result.get('data', [])
            if notes_data:
                formatted_notes = []
                for note in notes_data:
                    if isinstance(note, (list, tuple)) and len(note) >= 5:
                        pitch, start_time, duration, velocity, mute = note[:5]
                        mute_status = " (muted)" if mute else ""
                        formatted_notes.append(f"Pitch: {pitch}, Start: {start_time}, Duration: {duration}, Velocity: {velocity}{mute_status}")
                    else:
                        formatted_notes.append(f"Note: {note}")

                return f"MIDI notes in track {track_id}, clip {clip_id}:\n" + "\n".join(formatted_notes)
            else:
                return f"No MIDI notes found in track {track_id}, clip {clip_id}"
        return f"Unable to get notes from track {track_id}, clip {clip_id}"
    else:
        return f"Error getting clip notes: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def create_midi_track(index: int = -1) -> str:
    """
    Create a new MIDI track in Ableton Live.

    Args:
        index: Position to insert the track (-1 = end of list, default -1)

    Returns:
        Success message with new track index or error message
    """
    params = {
        "address": "/live/song/create_midi_track",
        "args": [index]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            track_index = result.get('data')
            if track_index is not None:
                return f"Successfully created MIDI track at index {track_index}"
            else:
                return "Successfully created MIDI track"
        return "MIDI track created successfully"
    else:
        return f"Error creating MIDI track: {response.get('message', 'Unknown error')}"

# ===== PLAYBACK CONTROL =====

@mcp.tool()
async def start_playing() -> str:
    """
    Start session playback in Ableton Live.

    Returns:
        Success or error message
    """
    params = {
        "address": "/live/song/start_playing",
        "args": []
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return "Session playback started"
    else:
        return f"Error starting playback: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def stop_playing() -> str:
    """
    Stop session playback in Ableton Live.

    Returns:
        Success or error message
    """
    params = {
        "address": "/live/song/stop_playing",
        "args": []
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return "Session playback stopped"
    else:
        return f"Error stopping playback: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def continue_playing() -> str:
    """
    Resume session playback in Ableton Live.

    Returns:
        Success or error message
    """
    params = {
        "address": "/live/song/continue_playing",
        "args": []
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return "Session playback resumed"
    else:
        return f"Error resuming playback: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def stop_all_clips() -> str:
    """
    Stop all clips from playing in Ableton Live.

    Returns:
        Success or error message
    """
    params = {
        "address": "/live/song/stop_all_clips",
        "args": []
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return "All clips stopped"
    else:
        return f"Error stopping all clips: {response.get('message', 'Unknown error')}"

# ===== TRACK/SCENE CREATION AND DELETION =====

@mcp.tool()
async def create_audio_track(index: int = -1) -> str:
    """
    Create a new audio track in Ableton Live.

    Args:
        index: Position to insert the track (-1 = end of list, default -1)

    Returns:
        Success message with new track index or error message
    """
    params = {
        "address": "/live/song/create_audio_track",
        "args": [index]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            track_index = result.get('data')
            if track_index is not None:
                return f"Successfully created audio track at index {track_index}"
            else:
                return "Successfully created audio track"
        return "Audio track created successfully"
    else:
        return f"Error creating audio track: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def create_return_track() -> str:
    """
    Create a new return track in Ableton Live.

    Returns:
        Success message or error message
    """
    params = {
        "address": "/live/song/create_return_track",
        "args": []
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return "Successfully created return track"
    else:
        return f"Error creating return track: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def create_scene(index: int = -1) -> str:
    """
    Create a new scene in Ableton Live.

    Args:
        index: Position to insert the scene (-1 = end of list, default -1)

    Returns:
        Success message with new scene index or error message
    """
    params = {
        "address": "/live/song/create_scene",
        "args": [index]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            scene_index = result.get('data')
            if scene_index is not None:
                return f"Successfully created scene at index {scene_index}"
            else:
                return "Successfully created scene"
        return "Scene created successfully"
    else:
        return f"Error creating scene: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def delete_track(track_index: int) -> str:
    """
    Delete a track in Ableton Live.

    Args:
        track_index: Index of the track to delete (0-based)

    Returns:
        Success or error message
    """
    params = {
        "address": "/live/song/delete_track",
        "args": [track_index]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return f"Successfully deleted track at index {track_index}"
    else:
        return f"Error deleting track: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def delete_return_track(track_index: int) -> str:
    """
    Delete a return track in Ableton Live.

    Args:
        track_index: Index of the return track to delete (0-based)

    Returns:
        Success or error message
    """
    params = {
        "address": "/live/song/delete_return_track",
        "args": [track_index]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return f"Successfully deleted return track at index {track_index}"
    else:
        return f"Error deleting return track: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def delete_scene(scene_index: int) -> str:
    """
    Delete a scene in Ableton Live.

    Args:
        scene_index: Index of the scene to delete (0-based)

    Returns:
        Success or error message
    """
    params = {
        "address": "/live/song/delete_scene",
        "args": [scene_index]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return f"Successfully deleted scene at index {scene_index}"
    else:
        return f"Error deleting scene: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def duplicate_track(track_index: int) -> str:
    """
    Duplicate a track in Ableton Live.

    Args:
        track_index: Index of the track to duplicate (0-based)

    Returns:
        Success or error message
    """
    params = {
        "address": "/live/song/duplicate_track",
        "args": [track_index]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return f"Successfully duplicated track at index {track_index}"
    else:
        return f"Error duplicating track: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def duplicate_scene(scene_index: int) -> str:
    """
    Duplicate a scene in Ableton Live.

    Args:
        scene_index: Index of the scene to duplicate (0-based)

    Returns:
        Success or error message
    """
    params = {
        "address": "/live/song/duplicate_scene",
        "args": [scene_index]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return f"Successfully duplicated scene at index {scene_index}"
    else:
        return f"Error duplicating scene: {response.get('message', 'Unknown error')}"

# ===== NAVIGATION AND CUE POINTS =====

@mcp.tool()
async def jump_by(time: float) -> str:
    """
    Jump song position by the specified time in beats.

    Args:
        time: Time to jump by, in beats (can be negative to jump backwards)

    Returns:
        Success or error message
    """
    params = {
        "address": "/live/song/jump_by",
        "args": [time]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        direction = "forward" if time >= 0 else "backward"
        return f"Successfully jumped {direction} by {abs(time)} beats"
    else:
        return f"Error jumping by time: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def jump_to_next_cue() -> str:
    """
    Jump to the next cue marker in Ableton Live.

    Returns:
        Success or error message
    """
    params = {
        "address": "/live/song/jump_to_next_cue",
        "args": []
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return "Successfully jumped to next cue marker"
    else:
        return f"Error jumping to next cue: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def jump_to_prev_cue() -> str:
    """
    Jump to the previous cue marker in Ableton Live.

    Returns:
        Success or error message
    """
    params = {
        "address": "/live/song/jump_to_prev_cue",
        "args": []
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return "Successfully jumped to previous cue marker"
    else:
        return f"Error jumping to previous cue: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def jump_to_cue_point(cue_point: str) -> str:
    """
    Jump to a specific cue point by name or numeric index.

    Args:
        cue_point: Cue point name or numeric index (as string)

    Returns:
        Success or error message
    """
    params = {
        "address": "/live/song/cue_point/jump",
        "args": [cue_point]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return f"Successfully jumped to cue point: {cue_point}"
    else:
        return f"Error jumping to cue point: {response.get('message', 'Unknown error')}"

# ===== OTHER SONG API FUNCTIONS =====

@mcp.tool()
async def undo() -> str:
    """
    Undo the last operation in Ableton Live.

    Returns:
        Success or error message
    """
    params = {
        "address": "/live/song/undo",
        "args": []
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return "Successfully undid last operation"
    else:
        return f"Error undoing operation: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def redo() -> str:
    """
    Redo the last undone operation in Ableton Live.

    Returns:
        Success or error message
    """
    params = {
        "address": "/live/song/redo",
        "args": []
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return "Successfully redid last operation"
    else:
        return f"Error redoing operation: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def capture_midi() -> str:
    """
    Capture MIDI in Ableton Live.

    Returns:
        Success or error message
    """
    params = {
        "address": "/live/song/capture_midi",
        "args": []
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return "Successfully captured MIDI"
    else:
        return f"Error capturing MIDI: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def tap_tempo() -> str:
    """
    Mimics a tap of the "Tap Tempo" button in Ableton Live.

    Returns:
        Success or error message
    """
    params = {
        "address": "/live/song/tap_tempo",
        "args": []
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return "Successfully tapped tempo"
    else:
        return f"Error tapping tempo: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def trigger_session_record() -> str:
    """
    Triggers record in session mode in Ableton Live.

    Returns:
        Success or error message
    """
    params = {
        "address": "/live/song/trigger_session_record",
        "args": []
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return "Successfully triggered session record"
    else:
        return f"Error triggering session record: {response.get('message', 'Unknown error')}"

# ===== SONG PROPERTIES - GETTERS =====

@mcp.tool()
async def get_is_playing() -> str:
    """
    Query whether the song is currently playing.

    Returns:
        Playing status or error message
    """
    params = {
        "address": "/live/song/get/is_playing",
        "args": []
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            is_playing = result.get('data')
            if is_playing is not None:
                return f"Song is playing: {is_playing}"
        return "Unable to get playing status"
    else:
        return f"Error getting playing status: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def get_current_song_time() -> str:
    """
    Query the current song time in beats.

    Returns:
        Current song time or error message
    """
    params = {
        "address": "/live/song/get/current_song_time",
        "args": []
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            time = result.get('data')
            if time is not None:
                return f"Current song time: {time} beats"
        return "Unable to get current song time"
    else:
        return f"Error getting current song time: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def get_song_length() -> str:
    """
    Query the song arrangement length in beats.

    Returns:
        Song length or error message
    """
    params = {
        "address": "/live/song/get/song_length",
        "args": []
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            length = result.get('data')
            if length is not None:
                return f"Song length: {length} beats"
        return "Unable to get song length"
    else:
        return f"Error getting song length: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def get_metronome() -> str:
    """
    Query metronome on/off status.

    Returns:
        Metronome status or error message
    """
    params = {
        "address": "/live/song/get/metronome",
        "args": []
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            metronome = result.get('data')
            if metronome is not None:
                return f"Metronome is {'on' if metronome else 'off'}"
        return "Unable to get metronome status"
    else:
        return f"Error getting metronome status: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def get_loop_status() -> str:
    """
    Query whether the song is currently looping.

    Returns:
        Loop status or error message
    """
    params = {
        "address": "/live/song/get/loop",
        "args": []
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            loop = result.get('data')
            if loop is not None:
                return f"Loop is {'on' if loop else 'off'}"
        return "Unable to get loop status"
    else:
        return f"Error getting loop status: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def get_loop_start() -> str:
    """
    Query the current loop start point.

    Returns:
        Loop start point or error message
    """
    params = {
        "address": "/live/song/get/loop_start",
        "args": []
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            loop_start = result.get('data')
            if loop_start is not None:
                return f"Loop start: {loop_start} beats"
        return "Unable to get loop start"
    else:
        return f"Error getting loop start: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def get_loop_length() -> str:
    """
    Query the current loop length.

    Returns:
        Loop length or error message
    """
    params = {
        "address": "/live/song/get/loop_length",
        "args": []
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            loop_length = result.get('data')
            if loop_length is not None:
                return f"Loop length: {loop_length} beats"
        return "Unable to get loop length"
    else:
        return f"Error getting loop length: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def get_time_signature() -> str:
    """
    Query the current time signature.

    Returns:
        Time signature or error message
    """
    # Get numerator
    num_params = {
        "address": "/live/song/get/signature_numerator",
        "args": []
    }

    num_response = await ableton_client.send_rpc_request("send_message", num_params)
    if num_response['status'] != 'ok':
        return f"Error getting time signature numerator: {num_response.get('message', 'Unknown error')}"

    # Get denominator
    denom_params = {
        "address": "/live/song/get/signature_denominator",
        "args": []
    }

    denom_response = await ableton_client.send_rpc_request("send_message", denom_params)
    if denom_response['status'] != 'ok':
        return f"Error getting time signature denominator: {denom_response.get('message', 'Unknown error')}"

    num_result = num_response.get('result', {})
    denom_result = denom_response.get('result', {})

    if (isinstance(num_result, dict) and num_result.get('status') == 'success' and
        isinstance(denom_result, dict) and denom_result.get('status') == 'success'):
        numerator = num_result.get('data')
        denominator = denom_result.get('data')
        if numerator is not None and denominator is not None:
            return f"Time signature: {numerator}/{denominator}"

    return "Unable to get time signature"

# ===== SONG PROPERTIES - SETTERS =====

@mcp.tool()
async def set_metronome(enabled: bool) -> str:
    """
    Set metronome on/off status.

    Args:
        enabled: True to turn metronome on, False to turn it off

    Returns:
        Success or error message
    """
    params = {
        "address": "/live/song/set/metronome",
        "args": [1 if enabled else 0]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return f"Metronome turned {'on' if enabled else 'off'}"
    else:
        return f"Error setting metronome: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def set_loop_status(enabled: bool) -> str:
    """
    Set whether the song is looping.

    Args:
        enabled: True to enable loop, False to disable

    Returns:
        Success or error message
    """
    params = {
        "address": "/live/song/set/loop",
        "args": [1 if enabled else 0]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return f"Loop turned {'on' if enabled else 'off'}"
    else:
        return f"Error setting loop status: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def set_loop_start(start: float) -> str:
    """
    Set the loop start point.

    Args:
        start: Loop start point in beats

    Returns:
        Success or error message
    """
    params = {
        "address": "/live/song/set/loop_start",
        "args": [start]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return f"Loop start set to {start} beats"
    else:
        return f"Error setting loop start: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def set_loop_length(length: float) -> str:
    """
    Set the loop length.

    Args:
        length: Loop length in beats

    Returns:
        Success or error message
    """
    if length <= 0:
        return "Error: Loop length must be greater than 0"

    params = {
        "address": "/live/song/set/loop_length",
        "args": [length]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return f"Loop length set to {length} beats"
    else:
        return f"Error setting loop length: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def set_current_song_time(time: float) -> str:
    """
    Set the current song time (playhead position).

    Args:
        time: Song time in beats

    Returns:
        Success or error message
    """
    if time < 0:
        return "Error: Song time cannot be negative"

    params = {
        "address": "/live/song/set/current_song_time",
        "args": [time]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return f"Song time set to {time} beats"
    else:
        return f"Error setting song time: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def set_time_signature(numerator: int, denominator: int) -> str:
    """
    Set the time signature.

    Args:
        numerator: Time signature numerator (e.g., 4 for 4/4)
        denominator: Time signature denominator (e.g., 4 for 4/4)

    Returns:
        Success or error message
    """
    if numerator <= 0 or denominator <= 0:
        return "Error: Time signature values must be positive"

    # Set numerator
    num_params = {
        "address": "/live/song/set/signature_numerator",
        "args": [numerator]
    }

    num_response = await ableton_client.send_rpc_request("send_message", num_params)
    if num_response['status'] != 'ok':
        return f"Error setting time signature numerator: {num_response.get('message', 'Unknown error')}"

    # Set denominator
    denom_params = {
        "address": "/live/song/set/signature_denominator",
        "args": [denominator]
    }

    denom_response = await ableton_client.send_rpc_request("send_message", denom_params)
    if denom_response['status'] != 'ok':
        return f"Error setting time signature denominator: {denom_response.get('message', 'Unknown error')}"

    return f"Time signature set to {numerator}/{denominator}"

@mcp.tool()
async def set_session_record(enabled: bool) -> str:
    """
    Set session record on/off.

    Args:
        enabled: True to enable session record, False to disable

    Returns:
        Success or error message
    """
    params = {
        "address": "/live/song/set/session_record",
        "args": [1 if enabled else 0]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return f"Session record {'enabled' if enabled else 'disabled'}"
    else:
        return f"Error setting session record: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def set_arrangement_overdub(enabled: bool) -> str:
    """
    Set arrangement overdub on/off.

    Args:
        enabled: True to enable arrangement overdub, False to disable

    Returns:
        Success or error message
    """
    params = {
        "address": "/live/song/set/arrangement_overdub",
        "args": [1 if enabled else 0]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return f"Arrangement overdub {'enabled' if enabled else 'disabled'}"
    else:
        return f"Error setting arrangement overdub: {response.get('message', 'Unknown error')}"

# ===== SPECIAL SONG PROPERTIES =====

@mcp.tool()
async def get_cue_points() -> str:
    """
    Query a list of the song's cue points.

    Returns:
        List of cue points with names and times or error message
    """
    params = {
        "address": "/live/song/get/cue_points",
        "args": []
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            cue_points = result.get('data', [])
            if cue_points:
                formatted_cues = []
                for i in range(0, len(cue_points), 2):
                    if i + 1 < len(cue_points):
                        name = cue_points[i]
                        time = cue_points[i + 1]
                        formatted_cues.append(f"{name}: {time} beats")
                return "Cue points:\n" + "\n".join(formatted_cues)
            else:
                return "No cue points found"
        return "Unable to get cue points"
    else:
        return f"Error getting cue points: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def get_num_scenes() -> str:
    """
    Query the number of scenes in the song.

    Returns:
        Number of scenes or error message
    """
    params = {
        "address": "/live/song/get/num_scenes",
        "args": []
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            num_scenes = result.get('data')
            if num_scenes is not None:
                return f"Number of scenes: {num_scenes}"
        return "Unable to get number of scenes"
    else:
        return f"Error getting number of scenes: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def get_num_tracks() -> str:
    """
    Query the number of tracks in the song.

    Returns:
        Number of tracks or error message
    """
    params = {
        "address": "/live/song/get/num_tracks",
        "args": []
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            num_tracks = result.get('data')
            if num_tracks is not None:
                return f"Number of tracks: {num_tracks}"
        return "Unable to get number of tracks"
    else:
        return f"Error getting number of tracks: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def get_track_data(start_track: int, end_track: int, properties: List[str]) -> str:
    """
    Query bulk properties of multiple tracks/clips.

    Args:
        start_track: Starting track index (inclusive)
        end_track: Ending track index (exclusive)
        properties: List of properties to query (e.g., ["track.name", "clip.name", "clip.length"])

    Returns:
        Formatted track/clip data or error message
    """
    if start_track < 0 or end_track <= start_track:
        return "Error: Invalid track range"

    if not properties:
        return "Error: No properties specified"

    params = {
        "address": "/live/song/get/track_data",
        "args": [start_track, end_track] + properties
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            data = result.get('data', [])
            if data:
                # Format the data based on the number of properties and tracks
                num_tracks = end_track - start_track
                num_properties = len(properties)
                formatted_output = []

                # Assuming data comes back as a flat list in track order
                for track_idx in range(num_tracks):
                    track_num = start_track + track_idx
                    formatted_output.append(f"\nTrack {track_num}:")

                    # Extract properties for this track
                    for prop_idx, prop in enumerate(properties):
                        if prop.startswith("track."):
                            # Track properties appear once per track
                            value_idx = track_idx * num_properties + prop_idx
                            if value_idx < len(data):
                                formatted_output.append(f"  {prop}: {data[value_idx]}")
                        elif prop.startswith("clip.") or prop.startswith("clip_slot."):
                            # Clip properties appear for each clip slot
                            formatted_output.append(f"  {prop} values:")
                            # This is simplified - actual implementation would need to handle clip data properly

                return "\n".join(formatted_output)
            else:
                return "No track data found"
        return "Unable to get track data"
    else:
        return f"Error getting track data: {response.get('message', 'Unknown error')}"

# ===== TRACK API =====

# Track Methods
@mcp.tool()
async def stop_all_clips_on_track(track_id: int) -> str:
    """
    Stop all clips on a specific track.

    Args:
        track_id: Track index (0-based)

    Returns:
        Success or error message
    """
    params = {
        "address": "/live/track/stop_all_clips",
        "args": [track_id]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return f"All clips stopped on track {track_id}"
    else:
        return f"Error stopping clips on track: {response.get('message', 'Unknown error')}"

# Track Properties - Getters
@mcp.tool()
async def get_track_volume(track_id: int) -> str:
    """
    Query track volume.

    Args:
        track_id: Track index (0-based)

    Returns:
        Track volume or error message
    """
    params = {
        "address": "/live/track/get/volume",
        "args": [track_id]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            volume = result.get('data')
            if volume is not None:
                return f"Track {track_id} volume: {volume}"
        return f"Unable to get volume for track {track_id}"
    else:
        return f"Error getting track volume: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def get_track_panning(track_id: int) -> str:
    """
    Query track panning.

    Args:
        track_id: Track index (0-based)

    Returns:
        Track panning or error message
    """
    params = {
        "address": "/live/track/get/panning",
        "args": [track_id]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            panning = result.get('data')
            if panning is not None:
                return f"Track {track_id} panning: {panning}"
        return f"Unable to get panning for track {track_id}"
    else:
        return f"Error getting track panning: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def get_track_mute(track_id: int) -> str:
    """
    Query track mute status.

    Args:
        track_id: Track index (0-based)

    Returns:
        Track mute status or error message
    """
    params = {
        "address": "/live/track/get/mute",
        "args": [track_id]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            mute = result.get('data')
            if mute is not None:
                return f"Track {track_id} is {'muted' if mute else 'unmuted'}"
        return f"Unable to get mute status for track {track_id}"
    else:
        return f"Error getting track mute status: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def get_track_solo(track_id: int) -> str:
    """
    Query track solo status.

    Args:
        track_id: Track index (0-based)

    Returns:
        Track solo status or error message
    """
    params = {
        "address": "/live/track/get/solo",
        "args": [track_id]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            solo = result.get('data')
            if solo is not None:
                return f"Track {track_id} solo is {'on' if solo else 'off'}"
        return f"Unable to get solo status for track {track_id}"
    else:
        return f"Error getting track solo status: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def get_track_arm(track_id: int) -> str:
    """
    Query whether track is armed.

    Args:
        track_id: Track index (0-based)

    Returns:
        Track arm status or error message
    """
    params = {
        "address": "/live/track/get/arm",
        "args": [track_id]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            armed = result.get('data')
            if armed is not None:
                return f"Track {track_id} is {'armed' if armed else 'not armed'}"
        return f"Unable to get arm status for track {track_id}"
    else:
        return f"Error getting track arm status: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def get_track_send(track_id: int, send_id: int) -> str:
    """
    Query track send level.

    Args:
        track_id: Track index (0-based)
        send_id: Send index (0-based)

    Returns:
        Track send level or error message
    """
    params = {
        "address": "/live/track/get/send",
        "args": [track_id, send_id]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            send_value = result.get('data')
            if send_value is not None:
                return f"Track {track_id} send {send_id}: {send_value}"
        return f"Unable to get send {send_id} for track {track_id}"
    else:
        return f"Error getting track send: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def get_track_color(track_id: int) -> str:
    """
    Query track color.

    Args:
        track_id: Track index (0-based)

    Returns:
        Track color or error message
    """
    params = {
        "address": "/live/track/get/color",
        "args": [track_id]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            color = result.get('data')
            if color is not None:
                return f"Track {track_id} color: {color}"
        return f"Unable to get color for track {track_id}"
    else:
        return f"Error getting track color: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def get_track_playing_slot_index(track_id: int) -> str:
    """
    Query currently-playing slot index.

    Args:
        track_id: Track index (0-based)

    Returns:
        Playing slot index or error message
    """
    params = {
        "address": "/live/track/get/playing_slot_index",
        "args": [track_id]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            slot_index = result.get('data')
            if slot_index is not None:
                return f"Track {track_id} playing slot index: {slot_index}"
        return f"No slot playing on track {track_id}"
    else:
        return f"Error getting playing slot index: {response.get('message', 'Unknown error')}"

# Track Properties - Setters
@mcp.tool()
async def set_track_volume(track_id: int, volume: float) -> str:
    """
    Set track volume.

    Args:
        track_id: Track index (0-based)
        volume: Volume level (0.0 to 1.0)

    Returns:
        Success or error message
    """
    if volume < 0.0 or volume > 1.0:
        return "Error: Volume must be between 0.0 and 1.0"

    params = {
        "address": "/live/track/set/volume",
        "args": [track_id, volume]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return f"Track {track_id} volume set to {volume}"
    else:
        return f"Error setting track volume: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def set_track_panning(track_id: int, panning: float) -> str:
    """
    Set track panning.

    Args:
        track_id: Track index (0-based)
        panning: Panning value (-1.0 to 1.0, -1.0=left, 0.0=center, 1.0=right)

    Returns:
        Success or error message
    """
    if panning < -1.0 or panning > 1.0:
        return "Error: Panning must be between -1.0 and 1.0"

    params = {
        "address": "/live/track/set/panning",
        "args": [track_id, panning]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return f"Track {track_id} panning set to {panning}"
    else:
        return f"Error setting track panning: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def set_track_mute(track_id: int, mute: bool) -> str:
    """
    Set track mute status.

    Args:
        track_id: Track index (0-based)
        mute: True to mute, False to unmute

    Returns:
        Success or error message
    """
    params = {
        "address": "/live/track/set/mute",
        "args": [track_id, 1 if mute else 0]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return f"Track {track_id} {'muted' if mute else 'unmuted'}"
    else:
        return f"Error setting track mute: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def set_track_solo(track_id: int, solo: bool) -> str:
    """
    Set track solo status.

    Args:
        track_id: Track index (0-based)
        solo: True to solo, False to unsolo

    Returns:
        Success or error message
    """
    params = {
        "address": "/live/track/set/solo",
        "args": [track_id, 1 if solo else 0]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return f"Track {track_id} solo {'on' if solo else 'off'}"
    else:
        return f"Error setting track solo: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def set_track_arm(track_id: int, arm: bool) -> str:
    """
    Set track arm status.

    Args:
        track_id: Track index (0-based)
        arm: True to arm, False to disarm

    Returns:
        Success or error message
    """
    params = {
        "address": "/live/track/set/arm",
        "args": [track_id, 1 if arm else 0]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return f"Track {track_id} {'armed' if arm else 'disarmed'}"
    else:
        return f"Error setting track arm: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def set_track_send(track_id: int, send_id: int, value: float) -> str:
    """
    Set track send level.

    Args:
        track_id: Track index (0-based)
        send_id: Send index (0-based)
        value: Send level (0.0 to 1.0)

    Returns:
        Success or error message
    """
    if value < 0.0 or value > 1.0:
        return "Error: Send value must be between 0.0 and 1.0"

    params = {
        "address": "/live/track/set/send",
        "args": [track_id, send_id, value]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return f"Track {track_id} send {send_id} set to {value}"
    else:
        return f"Error setting track send: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def set_track_color(track_id: int, color: int) -> str:
    """
    Set track color.

    Args:
        track_id: Track index (0-based)
        color: Color value (RGB color as integer)

    Returns:
        Success or error message
    """
    params = {
        "address": "/live/track/set/color",
        "args": [track_id, color]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        return f"Track {track_id} color set to {color}"
    else:
        return f"Error setting track color: {response.get('message', 'Unknown error')}"

# Track: Properties of multiple clips
@mcp.tool()
async def get_track_clip_names(track_id: int) -> str:
    """
    Query all clip names on a track.

    Args:
        track_id: Track index (0-based)

    Returns:
        List of clip names or error message
    """
    params = {
        "address": "/live/track/get/clips/name",
        "args": [track_id]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            clip_names = result.get('data', [])
            if clip_names:
                formatted_names = []
                for i, name in enumerate(clip_names):
                    if name:  # Only include non-empty names
                        formatted_names.append(f"Clip {i}: {name}")
                    else:
                        formatted_names.append(f"Clip {i}: (empty)")
                return f"Clip names on track {track_id}:\n" + "\n".join(formatted_names)
            else:
                return f"No clips found on track {track_id}"
        return f"Unable to get clip names for track {track_id}"
    else:
        return f"Error getting clip names: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def get_track_clip_lengths(track_id: int) -> str:
    """
    Query all clip lengths on a track.

    Args:
        track_id: Track index (0-based)

    Returns:
        List of clip lengths or error message
    """
    params = {
        "address": "/live/track/get/clips/length",
        "args": [track_id]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            clip_lengths = result.get('data', [])
            if clip_lengths:
                formatted_lengths = []
                for i, length in enumerate(clip_lengths):
                    if length is not None and length > 0:
                        formatted_lengths.append(f"Clip {i}: {length} beats")
                    else:
                        formatted_lengths.append(f"Clip {i}: (empty)")
                return f"Clip lengths on track {track_id}:\n" + "\n".join(formatted_lengths)
            else:
                return f"No clips found on track {track_id}"
        return f"Unable to get clip lengths for track {track_id}"
    else:
        return f"Error getting clip lengths: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def get_track_clip_colors(track_id: int) -> str:
    """
    Query all clip colors on a track.

    Args:
        track_id: Track index (0-based)

    Returns:
        List of clip colors or error message
    """
    params = {
        "address": "/live/track/get/clips/color",
        "args": [track_id]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            clip_colors = result.get('data', [])
            if clip_colors:
                formatted_colors = []
                for i, color in enumerate(clip_colors):
                    if color is not None:
                        formatted_colors.append(f"Clip {i}: {color}")
                    else:
                        formatted_colors.append(f"Clip {i}: (empty)")
                return f"Clip colors on track {track_id}:\n" + "\n".join(formatted_colors)
            else:
                return f"No clips found on track {track_id}"
        return f"Unable to get clip colors for track {track_id}"
    else:
        return f"Error getting clip colors: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def get_track_arrangement_clip_names(track_id: int) -> str:
    """
    Query all arrangement view clip names on a track.

    Args:
        track_id: Track index (0-based)

    Returns:
        List of arrangement clip names or error message
    """
    params = {
        "address": "/live/track/get/arrangement_clips/name",
        "args": [track_id]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            clip_names = result.get('data', [])
            if clip_names:
                formatted_names = []
                for i, name in enumerate(clip_names):
                    if name:
                        formatted_names.append(f"Arrangement Clip {i}: {name}")
                    else:
                        formatted_names.append(f"Arrangement Clip {i}: (unnamed)")
                return f"Arrangement clip names on track {track_id}:\n" + "\n".join(formatted_names)
            else:
                return f"No arrangement clips found on track {track_id}"
        return f"Unable to get arrangement clip names for track {track_id}"
    else:
        return f"Error getting arrangement clip names: {response.get('message', 'Unknown error')}"

# Track: Properties of devices
@mcp.tool()
async def get_track_num_devices(track_id: int) -> str:
    """
    Query the number of devices on a track.

    Args:
        track_id: Track index (0-based)

    Returns:
        Number of devices or error message
    """
    params = {
        "address": "/live/track/get/num_devices",
        "args": [track_id]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            num_devices = result.get('data')
            if num_devices is not None:
                return f"Track {track_id} has {num_devices} devices"
        return f"Unable to get device count for track {track_id}"
    else:
        return f"Error getting device count: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def get_track_device_names(track_id: int) -> str:
    """
    Query all device names on a track.

    Args:
        track_id: Track index (0-based)

    Returns:
        List of device names or error message
    """
    params = {
        "address": "/live/track/get/devices/name",
        "args": [track_id]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            device_names = result.get('data', [])
            if device_names:
                formatted_names = []
                for i, name in enumerate(device_names):
                    if name:
                        formatted_names.append(f"Device {i}: {name}")
                    else:
                        formatted_names.append(f"Device {i}: (unnamed)")
                return f"Device names on track {track_id}:\n" + "\n".join(formatted_names)
            else:
                return f"No devices found on track {track_id}"
        return f"Unable to get device names for track {track_id}"
    else:
        return f"Error getting device names: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def get_track_device_types(track_id: int) -> str:
    """
    Query all device types on a track.

    Args:
        track_id: Track index (0-based)

    Returns:
        List of device types or error message
    """
    params = {
        "address": "/live/track/get/devices/type",
        "args": [track_id]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            device_types = result.get('data', [])
            if device_types:
                formatted_types = []
                for i, device_type in enumerate(device_types):
                    if device_type:
                        formatted_types.append(f"Device {i}: {device_type}")
                    else:
                        formatted_types.append(f"Device {i}: (unknown type)")
                return f"Device types on track {track_id}:\n" + "\n".join(formatted_types)
            else:
                return f"No devices found on track {track_id}"
        return f"Unable to get device types for track {track_id}"
    else:
        return f"Error getting device types: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def get_track_device_class_names(track_id: int) -> str:
    """
    Query all device class names on a track.

    Args:
        track_id: Track index (0-based)

    Returns:
        List of device class names or error message
    """
    params = {
        "address": "/live/track/get/devices/class_name",
        "args": [track_id]
    }

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response.get('result', {})
        if isinstance(result, dict) and result.get('status') == 'success':
            class_names = result.get('data', [])
            if class_names:
                formatted_classes = []
                for i, class_name in enumerate(class_names):
                    if class_name:
                        formatted_classes.append(f"Device {i}: {class_name}")
                    else:
                        formatted_classes.append(f"Device {i}: (unknown class)")
                return f"Device class names on track {track_id}:\n" + "\n".join(formatted_classes)
            else:
                return f"No devices found on track {track_id}"
        return f"Unable to get device class names for track {track_id}"
    else:
        return f"Error getting device class names: {response.get('message', 'Unknown error')}"

if __name__ == "__main__":
    try:
        mcp.run()
    finally:
        asyncio.run(ableton_client.close())
