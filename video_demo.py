import socket
import json
import struct
import time
import threading
import cv2
import numpy as np
import subprocess as sp
from collections import deque

HOST = '192.168.1.1'
CTP_PORT = 3333
VIDEO_PORT = 2224
VIDEO_WIDTH = 1280
VIDEO_HEIGHT = 720

send_lock = threading.Lock()
shutdown_event = threading.Event()

# A thread-safe queue to pass packets from the network thread to the video thread
packet_queue = deque(maxlen=500)

# JSON commands for session setup and keep-alive, as per the company's demo
commands = {
    "APP_ACCESS": {
        "op": "PUT",
        "param": {"ver": "907", "type": "0"}
    },
    "OPEN_RT_STREAM": {
        "op": "PUT",
        "param": {
            "format": "1",
            "h": str(VIDEO_HEIGHT),
            "w": str(VIDEO_WIDTH),
            "fps": "25",
            "rate": "8000"
        }
    },
    "HEARTBEAT": {
        "op": "PUT",
        "param": {}
    }
}

def build_ctp_packet(topic, content):
    """
    Builds a CTP JSON packet with the header in network (big-endian) byte order.
    """
    header = b"CTP:"
    topic_bytes = topic.encode()
    content_json = json.dumps(content).encode()
    
    return b"".join([
        header,
        struct.pack('>h', len(topic_bytes)),
        topic_bytes,
        struct.pack('>i', len(content_json)),
        content_json
    ])

def udp_video_listener():
    """
    Listens for incoming UDP video stream data and puts packets into a queue.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        buffer_size = 65536
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, buffer_size)
        except Exception as e:
            print(f"⚠️ Could not set socket receive buffer size: {e}")

        sock.bind(('0.0.0.0', VIDEO_PORT))
        print(f"🎥 Video listener started on UDP port {VIDEO_PORT}")
        
        while not shutdown_event.is_set():
            try:
                data, addr = sock.recvfrom(buffer_size)
                if data:
                    packet_queue.append(data)
            except Exception as e:
                if not shutdown_event.is_set():
                    print(f"📹 Video listener error: {e}")
                break

def log_ffmpeg_errors(pipe):
    """
    Reads from FFmpeg's stderr and prints any errors.
    """
    try:
        for line in iter(pipe.readline, b''):
            line_str = line.decode('utf-8', errors='ignore').strip()
            if not line_str.startswith(('ffmpeg version', 'built with', 'configuration:', 'libavutil', 'libavcodec', 'libavformat', 'libavdevice', 'libavfilter', 'libswscale', 'libswresample')):
                 print(f"‼️ FFmpeg Error: {line_str}")
    except Exception as e:
        print(f"FFmpeg error logger failed: {e}")

def video_processing_and_display_thread():
    """
    Processes packets, reassembles frames, decodes using FFmpeg, and displays them.
    """
    frame_buffer = {}
    print("🎬 Video processing thread started")
    last_wait_message_time = time.time()

    ffmpeg_command = [
        'ffmpeg', '-y', '-f', 'h264', '-i', 'pipe:0',
        '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-vcodec', 'rawvideo', 'pipe:1'
    ]
    ffmpeg_process = sp.Popen(ffmpeg_command, stdin=sp.PIPE, stdout=sp.PIPE, stderr=sp.PIPE)
    
    threading.Thread(target=log_ffmpeg_errors, args=(ffmpeg_process.stderr,), daemon=True).start()

    try:
        while not shutdown_event.is_set():
            if not packet_queue:
                if time.time() - last_wait_message_time > 5.0:
                    print("⌛ Waiting for video data...")
                    last_wait_message_time = time.time()
                time.sleep(0.01)
                continue

            data = packet_queue.popleft()

            if len(data) < 20: continue
            
            (stream_type, reserved, payload_len,
             sequence, frame_size, offset, timestamp) = struct.unpack('<BBHIIII', data[:20])
            
            payload = data[20:20 + payload_len]

            if frame_size == 0 or frame_size > 150000: continue

            if timestamp not in frame_buffer:
                frame_buffer[timestamp] = {
                    'data': bytearray(frame_size), 'size': frame_size,
                    'received': 0, 'timestamp': time.time()
                }
            
            current_frame = frame_buffer[timestamp]

            if offset + len(payload) <= current_frame['size']:
                current_frame['data'][offset:offset + len(payload)] = payload
                current_frame['received'] += len(payload)
            
            if current_frame['received'] == current_frame['size']:
                complete_frame_data = bytes(current_frame['data'])
                
                if complete_frame_data.startswith(b'\x00\x00\x01') or complete_frame_data.startswith(b'\x00\x00\x00\x01'):
                    try:
                        ffmpeg_process.stdin.write(complete_frame_data)
                        ffmpeg_process.stdin.flush()
                        raw_image = ffmpeg_process.stdout.read(VIDEO_WIDTH * VIDEO_HEIGHT * 3)
                        
                        if len(raw_image) == VIDEO_WIDTH * VIDEO_HEIGHT * 3:
                            image = np.frombuffer(raw_image, dtype='uint8').reshape((VIDEO_HEIGHT, VIDEO_WIDTH, 3))
                            cv2.imshow('Drone Video Feed', image)
                    except Exception as e:
                        break
                del frame_buffer[timestamp]

            if cv2.waitKey(1) & 0xFF == ord('q'):
                shutdown_event.set()
                break
    finally:
        if 'ffmpeg_process' in locals() and ffmpeg_process.poll() is None:
            ffmpeg_process.kill()
        cv2.destroyAllWindows()
        print("🛑 Video display window closed.")

def json_heartbeat_thread(sock, event):
    """
    Sends the JSON CTP_KEEP_ALIVE packet continuously to maintain the session.
    """
    print("💓 JSON heartbeat thread started.")
    packet = build_ctp_packet("CTP_KEEP_ALIVE", commands["HEARTBEAT"])

    while not event.is_set():
        try:
            with send_lock:
                sock.sendall(packet)
        except Exception as e:
            if not event.is_set():
                print(f"💔 JSON Heartbeat failed: {e}")
            event.set()
            break
        time.sleep(0.9)

def tcp_response_listener(sock, event):
    """
    Continuously drains the TCP socket to prevent buffer overflows and detect disconnects.
    """
    print("👂 TCP response listener started.")
    try:
        while not event.is_set():
            sock.settimeout(1.0)
            try:
                # The only job of this listener is to receive data.
                # This keeps the drone's send buffer from filling up.
                chunk = sock.recv(4096)
                if not chunk:
                    if not event.is_set():
                        print("🔌 Drone closed the TCP connection.")
                    event.set()
                    break
            except socket.timeout:
                continue
            except Exception as e:
                if not event.is_set():
                    print(f"❌ TCP listener error: {e}")
                event.set()
                break
    finally:
        print("🛑 TCP response listener stopped.")


def main():
    """
    Main function to connect to the drone and display video.
    """
    print("--- Drone Video Viewer Initializing ---")
    
    # Start background threads that run for the life of the program
    threading.Thread(target=udp_video_listener, daemon=True).start()
    video_thread = threading.Thread(target=video_processing_and_display_thread, daemon=True)
    video_thread.start()

    while not shutdown_event.is_set():
        tcp_sock = None
        connection_event = threading.Event()
        try:
            print(f"Attempting to connect to {HOST}:{CTP_PORT}...")
            tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            tcp_sock.connect((HOST, CTP_PORT))
            print(f"🔌 Connection successful!")

            # Start listener thread first to catch all responses
            listener = threading.Thread(target=tcp_response_listener, args=(tcp_sock, connection_event), daemon=True)
            listener.start()
            
            # Send session setup commands
            app_access_pkt = build_ctp_packet("APP_ACCESS", commands["APP_ACCESS"])
            tcp_sock.sendall(app_access_pkt)
            print("📨 APP_ACCESS command sent.")
            time.sleep(0.1)

            video_pkt = build_ctp_packet("OPEN_RT_STREAM", commands["OPEN_RT_STREAM"])
            tcp_sock.sendall(video_pkt)
            print("📡 OPEN_RT_STREAM command sent.")
            
            # FIX: Start the heartbeat thread AFTER the setup commands are sent.
            # This prevents a race condition where a heartbeat is sent before the session is established.
            hb_thread = threading.Thread(target=json_heartbeat_thread, args=(tcp_sock, connection_event), daemon=True)
            hb_thread.start()

            # Wait until this connection is lost or the program exits
            connection_event.wait()

        except (socket.error, KeyboardInterrupt) as e:
            print(f"Encountered an error: {e}")
            shutdown_event.set() # Exit the whole program on major error
        finally:
            if tcp_sock:
                tcp_sock.close()
            
            if not shutdown_event.is_set():
                print("🔁 Connection lost. Attempting to reconnect in 5 seconds...")
                time.sleep(5)
    
    print("--- Program Exiting ---")


if __name__ == "__main__":
    main()
