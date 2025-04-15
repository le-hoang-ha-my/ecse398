import sys
import os
import json
import asyncio
import threading
import time
import matplotlib.pyplot as plt
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QPushButton, QLabel, QCheckBox,
    QFileDialog, QHBoxLayout, QScrollArea, QMessageBox, QGraphicsDropShadowEffect,
    QProgressBar, QComboBox, QSpinBox, QGroupBox, QFormLayout, QLineEdit
)
from PyQt5.QtGui import QFont, QIcon, QPixmap, QPalette, QBrush, QColor
from PyQt5.QtCore import Qt, pyqtSignal, QObject, QThread
from bleak import BleakScanner, BleakClient


# Constants for BLE communication
BLE_DEVICE_NAME = "ESP32-BLE-Sender"
BLE_SERVICE_UUID = "0000FFE0-0000-1000-8000-00805F9B34FB"
BLE_CHARACTERISTIC_UUID = "0000FFE1-0000-1000-8000-00805F9B34FB"


# Custom signal emitter for Bluetooth events
class BluetoothSignals(QObject):
    device_found = pyqtSignal(str)
    connection_status = pyqtSignal(bool, str)
    data_received = pyqtSignal(str)
    chunks_complete = pyqtSignal(list)


class BluetoothWorker(QThread):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.signals = BluetoothSignals()
        self.client = None
        self.device_name = BLE_DEVICE_NAME
        self.device_address = None
        self.is_scanning = True
        self.is_connected = False
        self.loop = None
        self.received_chunks = []
        self.chunks_in_progress = False
        
    def run(self):
        """Main thread function that handles the asyncio event loop"""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.scan_and_connect())
        self.loop.run_forever()

    async def scan_and_connect(self):
        """Scan for BLE devices and connect to the target device"""
        while self.is_scanning:
            try:
                self.signals.connection_status.emit(False, "Scanning for BLE device...")
                
                # Scan for devices
                devices = await BleakScanner.discover()
                
                for device in devices:
                    if device.name and self.device_name in device.name:
                        self.device_address = device.address
                        self.signals.device_found.emit(f"Found {device.name} at {self.device_address}")
                        
                        # Stop scanning and connect
                        self.is_scanning = False
                        await self.connect_to_device()
                        return
                
                # Wait before scanning again
                await asyncio.sleep(2)
                
            except Exception as e:
                self.signals.connection_status.emit(False, f"Scan error: {str(e)}")
                await asyncio.sleep(5)  # Wait before retrying
    
    async def connect_to_device(self):
        """Connect to the BLE device and set up notifications"""
        try:
            self.signals.connection_status.emit(False, f"Connecting to {self.device_name}...")
            
            # Create client and connect
            self.client = BleakClient(self.device_address)
            await self.client.connect()
            
            if self.client.is_connected:
                self.is_connected = True
                self.signals.connection_status.emit(True, f"Connected to {self.device_name}")
                
                # Start listening for notifications
                await self.client.start_notify(BLE_CHARACTERISTIC_UUID, self.notification_handler)
                
                # Start monitoring connection
                asyncio.create_task(self.connection_monitor())
            else:
                self.signals.connection_status.emit(False, "Connection failed")
                self.is_scanning = True
                asyncio.create_task(self.scan_and_connect())
                
        except Exception as e:
            self.signals.connection_status.emit(False, f"Connection error: {str(e)}")
            self.is_scanning = True
            await asyncio.sleep(5)
            asyncio.create_task(self.scan_and_connect())
    
    def notification_handler(self, sender, data):
        """Handle incoming BLE notifications/chunks"""
        try:
            # Decode the received chunk
            chunk = data.decode('utf-8')
            self.signals.data_received.emit(chunk)
            
            # Add to our chunks list
            self.received_chunks.append(chunk)
            
            # Check if this is the end of the data stream (simple heuristic)
            # A better approach would be if the BLE device indicated this is the last chunk
            # For now, we'll use a timeout-based approach in the connection_monitor
            
        except Exception as e:
            print(f"Error processing notification: {str(e)}")
    
    async def connection_monitor(self):
        """Monitor the connection status and reconnect if needed"""
        chunk_timeout = 1.0  # Time to wait for more chunks before considering transmission complete
        last_chunk_time = 0
        
        while True:
            if not self.is_connected or not self.client.is_connected:
                self.is_connected = False
                self.signals.connection_status.emit(False, "Connection lost, reconnecting...")
                self.is_scanning = True
                await self.client.disconnect()
                self.client = None
                asyncio.create_task(self.scan_and_connect())
                return
            
            # Check if chunks reception has timed out
            if self.chunks_in_progress and self.received_chunks and time.time() - last_chunk_time > chunk_timeout:
                self.chunks_in_progress = False
                # Make a copy of the chunks to avoid race conditions
                chunks_copy = self.received_chunks.copy()
                self.received_chunks = []
                self.signals.chunks_complete.emit(chunks_copy)
            
            # Update last_chunk_time if we have received chunks
            if self.received_chunks and self.chunks_in_progress:
                last_chunk_time = time.time()
            
            await asyncio.sleep(0.1)
    
    async def send_command(self, battery_num, measurement_type, measurement_count):
        """Send command to the BLE device"""
        if not self.is_connected:
            self.signals.connection_status.emit(False, "Cannot send command: Not connected")
            return False
        
        try:
            # Format the command string
            command = f"#{battery_num};{measurement_type};#{measurement_count}"
            
            # Clear any previous chunks
            self.received_chunks = []
            self.chunks_in_progress = True
            
            # Send the command
            await self.client.write_gatt_char(BLE_CHARACTERISTIC_UUID, command.encode())
            self.signals.connection_status.emit(True, f"Sent command: {command}")
            return True
            
        except Exception as e:
            self.signals.connection_status.emit(True, f"Error sending command: {str(e)}")
            return False


class BatteryDataViewer(QWidget):
    def __init__(self):
        super().__init__()

        self.data = {
            "time": [],
        }
        self.timestamps = []
        self.selected_keys = []  # User-selected parameters
        self.current_battery = 1
        self.current_measurement_type = "Voltage"
        self.current_measurement_count = 3
        
        # Initialize Bluetooth worker
        self.bluetooth_worker = BluetoothWorker()
        self.setup_bluetooth_signals()
        self.bluetooth_worker.start()

        self.initUI()
        self.applyStyles()

    def setup_bluetooth_signals(self):
        """Connect Bluetooth worker signals to UI handlers"""
        self.bluetooth_worker.signals.device_found.connect(self.on_device_found)
        self.bluetooth_worker.signals.connection_status.connect(self.on_connection_status)
        self.bluetooth_worker.signals.data_received.connect(self.on_data_chunk_received)
        self.bluetooth_worker.signals.chunks_complete.connect(self.on_data_complete)

    def on_device_found(self, message):
        """Handle device found event"""
        self.status_label.setText(message)

    def on_connection_status(self, connected, message):
        """Handle connection status change"""
        self.status_label.setText(message)
        self.connection_indicator.setValue(100 if connected else 0)
        self.request_button.setEnabled(connected)

    def on_data_chunk_received(self, chunk):
        """Handle a single chunk of data from the BLE device"""
        self.data_label.setText(f"Receiving data... Latest chunk: {chunk}")

    def on_data_complete(self, chunks):
        """Process all received chunks once complete"""
        if not chunks:
            self.data_label.setText("No data received")
            return
        
        # Join all chunks together
        full_data = ''.join(chunks)
        self.data_label.setText(f"Received complete data: {full_data}")
        
        try:
            # Parse the data - expecting a list format
            if full_data.startswith('[') and full_data.endswith(']'):
                # Try to parse as JSON
                parsed_data = json.loads(full_data)
                
                # Add timestamp for when we received this data
                current_time = time.time()
                self.data["time"].append(current_time)
                
                # Add the measurement data with the current measurement type as the key
                measurement_key = self.current_measurement_type.lower()
                if measurement_key not in self.data:
                    self.data[measurement_key] = []
                
                # If parsed_data is a list, add each value
                if isinstance(parsed_data, list):
                    for value in parsed_data:
                        self.data[measurement_key].append(float(value))
                else:
                    # If it's a single value
                    self.data[measurement_key].append(float(parsed_data))
                
                # Update the UI to show we have new data
                self.update_checkboxes()
                self.show_recent()
            else:
                self.data_label.setText(f"Received data is not in the expected format: {full_data}")
        
        except json.JSONDecodeError:
            self.data_label.setText(f"Could not parse data as JSON: {full_data}")
        except Exception as e:
            self.data_label.setText(f"Error processing data: {str(e)}")

    def send_request(self):
        """Send a request to the BLE device based on current settings"""
        battery = self.battery_number.value()
        measurement_type = self.measurement_type.currentText()
        count = self.measurement_count.value()
        
        # Save current values for reference
        self.current_battery = battery
        self.current_measurement_type = measurement_type
        self.current_measurement_count = count
        
        # Make async call to send command
        def send():
            asyncio.run_coroutine_threadsafe(
                self.bluetooth_worker.send_command(battery, measurement_type, count),
                self.bluetooth_worker.loop
            )
        
        # Run in thread to avoid blocking UI
        threading.Thread(target=send).start()
        self.data_label.setText(f"Sending request to battery {battery} for {count} {measurement_type} measurements...")

    def initUI(self):
        """Initialize the GUI layout and components."""
        layout = QVBoxLayout()

        # Bluetooth status section
        bluetooth_layout = QHBoxLayout()
        
        self.status_label = QLabel("Starting Bluetooth scanner...")
        self.status_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.addGlowEffect(self.status_label)
        bluetooth_layout.addWidget(self.status_label, 7)
        
        self.connection_indicator = QProgressBar()
        self.connection_indicator.setRange(0, 100)
        self.connection_indicator.setValue(0)
        self.connection_indicator.setTextVisible(False)
        self.connection_indicator.setFixedWidth(100)
        bluetooth_layout.addWidget(self.connection_indicator, 1)
        
        layout.addLayout(bluetooth_layout)
        
        # Command input section
        command_group = QGroupBox("BLE Command Settings")
        command_layout = QFormLayout()
        
        # Battery number selector
        self.battery_number = QSpinBox()
        self.battery_number.setRange(1, 4)  # Assuming batteries 1-4
        self.battery_number.setValue(1)
        command_layout.addRow("Battery #:", self.battery_number)
        
        # Measurement type dropdown
        self.measurement_type = QComboBox()
        self.measurement_type.addItems(["Voltage", "Power", "Current", "Life"])
        command_layout.addRow("Measurement:", self.measurement_type)
        
        # Measurement count
        self.measurement_count = QSpinBox()
        self.measurement_count.setRange(1, 20)  # Reasonable range for measurements
        self.measurement_count.setValue(3)
        command_layout.addRow("# of Measurements:", self.measurement_count)
        
        command_group.setLayout(command_layout)
        layout.addWidget(command_group)
        
        # Request button
        self.request_button = QPushButton("🔌 Request Data from Battery")
        self.request_button.clicked.connect(self.send_request)
        self.request_button.setEnabled(False)  # Disabled until connected
        self.addGlowEffect(self.request_button)
        layout.addWidget(self.request_button)
        
        # Load data button
        self.load_button = QPushButton("📂 Load Battery Data")
        self.load_button.clicked.connect(self.load_data)
        self.addGlowEffect(self.load_button)
        layout.addWidget(self.load_button)

        # Display loaded data info
        self.data_label = QLabel("No data loaded.")
        self.data_label.setAlignment(Qt.AlignCenter)
        self.addGlowEffect(self.data_label)
        layout.addWidget(self.data_label)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.checkbox_container = QWidget()
        self.checkbox_container.setStyleSheet("background-color: rgba(30, 30, 30, 160);")
        self.checkbox_layout = QVBoxLayout(self.checkbox_container)
        self.scroll_area.setWidget(self.checkbox_container)
        layout.addWidget(self.scroll_area)

        # Buttons to show data
        button_layout = QHBoxLayout()

        self.show_recent_button = QPushButton("📊 Show Latest Data")
        self.show_recent_button.clicked.connect(self.show_recent)
        self.addGlowEffect(self.show_recent_button)
        button_layout.addWidget(self.show_recent_button)

        self.plot_button = QPushButton("📈 Plot Data Over Time")
        self.plot_button.clicked.connect(self.plot_data)
        self.addGlowEffect(self.plot_button)
        button_layout.addWidget(self.plot_button)

        self.export_button = QPushButton("💾 Export Data")
        self.export_button.clicked.connect(self.export_debug_json)
        self.addGlowEffect(self.export_button)
        button_layout.addWidget(self.export_button)

        layout.addLayout(button_layout)

        self.setLayout(layout)
        self.setWindowTitle("🔋 Battery Monitoring System")
        self.setGeometry(100, 100, 600, 550)  # Increased height for new controls

        self.setWindowIcon(QIcon(QPixmap(32, 32)))

        self.setBackgroundImage()

    def addGlowEffect(self, widget):
        glow = QGraphicsDropShadowEffect()
        glow.setBlurRadius(10)
        glow.setColor(QColor("#FFFFFF"))
        glow.setOffset(0)
        widget.setGraphicsEffect(glow)

    def setBackgroundImage(self):
        # If you want to keep the background image feature
        # Adjust path or remove if not needed
        image_path = "background.jpg"
        if os.path.exists(image_path):
            pixmap = QPixmap(image_path)
            scaled_pixmap = pixmap.scaled(self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            palette = self.palette()
            palette.setBrush(QPalette.Background, QBrush(scaled_pixmap))
            self.setPalette(palette)
        else:
            # Set a default background color
            self.setStyleSheet("background-color: #2E3440;")

    def resizeEvent(self, event):
        self.setBackgroundImage()
        super().resizeEvent(event)

    def applyStyles(self):
        self.setStyleSheet("""
            QWidget {
                background-color: #2E3440;
                color: #D8DEE9;
            }
            QPushButton {
                background-color: rgba(58, 63, 68, 200);
                border: 2px solid #61AFEF;
                padding: 8px;
                border-radius: 5px;
                font-weight: bold;
                font-size: 14px;
                color: #D8DEE9;
            }
            QPushButton:hover {
                background-color: #61AFEF;
                color: #2E3440;
            }
            QPushButton:disabled {
                background-color: rgba(58, 63, 68, 100);
                border: 2px solid #555;
                color: #777;
            }
            QLabel {
                font-size: 16px;
                font-weight: bold;
                color: #D8DEE9;
                background-color: rgba(30, 30, 30, 180);
                padding: 8px;
                border-radius: 8px;
            }
            QCheckBox {
                font-size: 14px;
                padding: 3px;
                color: #D8DEE9;
                background-color: rgba(30, 30, 30, 180);
                border-radius: 5px;
                padding: 5px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
            }
            QProgressBar {
                border: 2px solid #61AFEF;
                border-radius: 5px;
                text-align: center;
                background-color: rgba(30, 30, 30, 180);
            }
            QProgressBar::chunk {
                background-color: #42A5F5;
                width: 1px;
            }
            QGroupBox {
                font-size: 14px;
                font-weight: bold;
                border: 2px solid #61AFEF;
                border-radius: 6px;
                margin-top: 1ex;
                padding: 10px;
                background-color: rgba(30, 30, 30, 180);
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top center;
                padding: 0 5px;
                color: #88C0D0;
            }
            QComboBox, QSpinBox {
                border: 1px solid #61AFEF;
                border-radius: 3px;
                padding: 5px;
                background-color: rgba(40, 40, 40, 200);
                color: #D8DEE9;
                min-height: 25px;
            }
            QComboBox::drop-down {
                border: 0px;
            }
            QComboBox::down-arrow {
                width: 14px;
                height: 14px;
            }
            QScrollArea {
                border: 2px solid #61AFEF;
                border-radius: 6px;
            }
        """)

        font = QFont("Arial", 12)
        self.data_label.setFont(font)

    def load_data(self):
        options = QFileDialog.Options()
        file_name, _ = QFileDialog.getOpenFileName(self, "Open JSON File", "data/", "JSON Files (*.json);;All Files (*)", options=options)

        if not file_name:
            QMessageBox.warning(self, "No File Selected", "⚠️ You must select a data file!", QMessageBox.Ok)
            return

        with open(file_name, 'r') as file:
            loaded_data = json.load(file)
        
        # Merge with existing data or replace it
        if not self.data or QMessageBox.question(
            self, 
            "Merge Data", 
            "Do you want to merge with existing data? Click No to replace.", 
            QMessageBox.Yes | QMessageBox.No
        ) == QMessageBox.No:
            self.data = loaded_data
        else:
            # Merge data - Add file data to existing data
            for key, values in loaded_data.items():
                if key not in self.data:
                    self.data[key] = values
                else:
                    self.data[key].extend(values)

        self.timestamps = self.data.get("time", [])
        self.update_checkboxes()
        self.data_label.setText(f"✅ Data loaded from {os.path.basename(file_name)}")

    def update_checkboxes(self):
        # Clear existing checkboxes
        for i in reversed(range(self.checkbox_layout.count())):
            widget = self.checkbox_layout.itemAt(i).widget()
            if widget is not None:
                widget.setParent(None)

        # Add checkboxes for each data type except time
        self.checkboxes = {}
        for key in self.data.keys():
            if key != "time":
                checkbox = QCheckBox(key)
                checkbox.setChecked(True)  # Default to checked
                self.addGlowEffect(checkbox)
                self.checkbox_layout.addWidget(checkbox)
                self.checkboxes[key] = checkbox

        if len(self.checkboxes) > 0:
            self.data_label.setText("✅ Data loaded. Select parameters to display.")

    def get_selected_keys(self):
        """Get the list of selected parameters."""
        self.selected_keys = [key for key, checkbox in self.checkboxes.items() if checkbox.isChecked()]

    def show_recent(self):
        """Display the most recent values of selected parameters."""
        self.get_selected_keys()
        if not self.selected_keys:
            self.data_label.setText("⚠️ No parameter selected.")
            return

        if not self.data.get("time"):
            self.data_label.setText("⚠️ No data available to display.")
            return

        # Find the most recent data point for each selected key
        display_text = "📊 Latest Battery Data:\n"
        
        for key in self.selected_keys:
            if key in self.data and self.data[key]:
                # Get the most recent value
                recent_value = self.data[key][-1]
                
                # Format the value nicely (handle different data types)
                if isinstance(recent_value, (int, float)):
                    # Format value based on measurement type
                    if "voltage" in key.lower():
                        display_text += f"{key}: {recent_value:.2f} V\n"
                    elif "current" in key.lower():
                        display_text += f"{key}: {recent_value:.2f} A\n"
                    elif "power" in key.lower():
                        display_text += f"{key}: {recent_value:.2f} W\n"
                    elif "life" in key.lower() or "battery" in key.lower():
                        display_text += f"{key}: {recent_value:.1f}%\n"
                    else:
                        display_text += f"{key}: {recent_value}\n"
                else:
                    display_text += f"{key}: {recent_value}\n"
        
        self.data_label.setText(display_text)

    def plot_data(self):
        self.get_selected_keys()
        if not self.selected_keys:
            self.data_label.setText("⚠️ No parameter selected for plotting.")
            return

        if not self.data.get("time") or len(self.data["time"]) == 0:
            self.data_label.setText("⚠️ No time data available for plotting.")
            return

        plt.figure(figsize=(10, 6))
        for key in self.selected_keys:
            if key in self.data and len(self.data[key]) > 0:
                # Convert timestamps to relative time (minutes from start)
                # This makes the plot more readable
                if len(self.data["time"]) > 0:
                    start_time = self.data["time"][0]
                    relative_times = [(t - start_time) / 60 for t in self.data["time"]]
                    
                    # Make sure we have at least as many data points as timestamps
                    data_points = self.data[key][:len(relative_times)]
                    
                    plt.plot(relative_times, data_points, label=key, marker='o')

        plt.xlabel("Time (minutes)")
        plt.ylabel("Values")
        plt.title("Battery Measurements Over Time")
        plt.legend()
        plt.grid(True)
        plt.tight_layout()
        plt.show()

    def export_debug_json(self):
        if not self.data or not self.data.get("time"):
            QMessageBox.information(self, "No Data", "❌ No data to export. Please load or request data first.")
            return

        options = QFileDialog.Options()
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Export Data",
            "battery_data.json",
            "JSON Files (*.json);;All Files (*)",
            options=options
        )

        if file_name:
            try:
                with open(file_name, 'w') as f:
                    json.dump(self.data, f, indent=4)
                QMessageBox.information(self, "Export Successful", f"✅ Data exported to:\n{file_name}")
            except Exception as e:
                QMessageBox.critical(self, "Export Failed", f"❌ Failed to export data:\n{str(e)}")
                
    def closeEvent(self, event):
        """Clean up when the window is closed"""
        # Stop Bluetooth worker thread
        if hasattr(self, 'bluetooth_worker') and self.bluetooth_worker.isRunning():
            # Stop the event loop
            if self.bluetooth_worker.loop:
                self.bluetooth_worker.loop.call_soon_threadsafe(
                    self.bluetooth_worker.loop.stop
                )
                
            # Wait for thread to finish
            self.bluetooth_worker.wait()
            
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    viewer = BatteryDataViewer()
    viewer.show()
    sys.exit(app.exec_())