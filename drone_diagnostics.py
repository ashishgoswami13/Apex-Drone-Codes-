import socket
import time
import logging
import sys

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Common drone IPs to try
DRONE_IPS = [
    "192.168.1.1",    # Original
    "192.168.10.1",   # Common for many drones
    "192.168.0.1",    # Another common one
    "192.168.2.1"     # Yet another possibility
]

# Common ports to try
COMMAND_PORTS = [3333, 8080, 8888, 7070, 9000]
VIDEO_PORTS = [2224, 6666, 5555, 7777, 8554]

def test_connection(ip, port):
    """Test UDP connection to a specific IP and port"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(1)
        sock.connect((ip, port))
        logger.info(f"✅ Successfully connected to {ip}:{port}")
        sock.close()
        return True
    except Exception as e:
        logger.info(f"❌ Cannot connect to {ip}:{port} - {e}")
        return False

def scan_network():
    """Scan network for potential drone connections"""
    logger.info("===== DRONE NETWORK DIAGNOSTICS =====")
    logger.info("Scanning network for drone...")
    
    for ip in DRONE_IPS:
        logger.info(f"\nTesting IP: {ip}")
        
        # Test ping
        try:
            import subprocess
            result = subprocess.run(['ping', '-n', '1', '-w', '1000', ip], 
                                    stdout=subprocess.PIPE, 
                                    stderr=subprocess.PIPE)
            if result.returncode == 0:
                logger.info(f"✅ Ping successful to {ip}")
            else:
                logger.info(f"❌ Ping failed to {ip}")
        except Exception as e:
            logger.info(f"⚠️ Couldn't perform ping test: {e}")
        
        # Test command ports
        for port in COMMAND_PORTS:
            test_connection(ip, port)
            
        # Test video ports
        for port in VIDEO_PORTS:
            test_connection(ip, port)
            
    logger.info("\n===== NETWORK INTERFACE INFORMATION =====")
    try:
        # Get local network interfaces
        interfaces = socket.getaddrinfo(host=socket.gethostname(), port=None, family=socket.AF_INET)
        for interface in interfaces:
            local_ip = interface[4][0]
            logger.info(f"Local IP: {local_ip}")
    except Exception as e:
        logger.info(f"⚠️ Couldn't get network interfaces: {e}")
        
    logger.info("\n===== RECOMMENDATIONS =====")
    logger.info("1. Make sure you're connected to the drone's WiFi network")
    logger.info("2. Check your drone's manual for the correct IP and port settings")
    logger.info("3. Try restarting the drone and reconnecting")
    logger.info("4. Update video_demo.py with the working IP/port combination")

if __name__ == "__main__":
    scan_network()
