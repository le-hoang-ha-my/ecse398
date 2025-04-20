import sys
import os
import json
import asyncio
import threading
import time
import re
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
            
            # Update the connection monitor so it knows we got a chunk
            self.chunks_in_progress = True
            
        except Exception as e:
            print(f"Error processing notification: {str(e)}")
    
    async def connection_monitor(self):
        """Monitor the connection status and reconnect if needed"""
        total_timeout = 30.0  # Maximum time (seconds) to wait for a complete transmission
        transmission_start_time = 0
        
        while True:
            if not self.is_connected or not self.client.is_connected:
                self.is_connected = False
                self.signals.connection_status.emit(False, "Connection lost, reconnecting...")
                self.is_scanning = True
                await self.client.disconnect()
                self.client = None
                asyncio.create_task(self.scan_and_connect())
                return
            
            current_time = time.time()
            
            # If we've started receiving chunks, update the start time
            if self.chunks_in_progress and self.received_chunks and transmission_start_time == 0:
                transmission_start_time = current_time
                self.signals.connection_status.emit(True, "Data transmission started")
            
            # Check if transmission should be considered complete
            if self.chunks_in_progress and self.received_chunks:
                # Conditions to consider transmission complete:
                # 1. Any chunk contains a closing bracket "]"
                # 2. Total transmission time has exceeded total_timeout seconds
                if (any("]" in chunk for chunk in self.received_chunks)) or \
                   (transmission_start_time > 0 and current_time - transmission_start_time > total_timeout):
                    
                    self.signals.connection_status.emit(True, "Data transmission complete")
                    self.chunks_in_progress = False
                    
                    # Make a copy of the chunks to avoid race conditions
                    chunks_copy = self.received_chunks.copy()
                    self.received_chunks = []
                    
                    # Reset transmission tracking
                    transmission_start_time = 0
                    
                    # Emit the complete signal with collected chunks
                    self.signals.chunks_complete.emit(chunks_copy)
            
            await asyncio.sleep(0.1)
    
    async def send_command(self, battery_num, measurement_type, measurement_count):
        """Send command to the BLE device"""
        if not self.is_connected:
            self.signals.connection_status.emit(False, "Cannot send command: Not connected")
            return False
        
        try:
            command = f"{battery_num};{measurement_type};{measurement_count}"
            
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
        self.current_measurement_count = 100
        
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
        self.data_label.setText(f"Receiving data... (Chunk received)")

    def on_data_complete(self, chunks):
        """Process all received chunks once complete"""
        if not chunks:
            self.data_label.setText("No data received")
            return
        
        # Join all chunks together
        full_data = ''.join(chunks)
        self.data_label.setText(f"Data received: {full_data}")
        
        try:
            # Check if the data is in a list format
            if full_data.strip() and ('[' in full_data or ']' in full_data):
                # Clean up the data - handle extra spaces and commas
                cleaned_data = self.clean_data_string(full_data)
                
                # Parse the data values
                values = self.parse_numeric_values(cleaned_data)
                
                base_time = time.time()
                
                # Add the measurement data with the current measurement type as the key
                measurement_key = self.current_measurement_type.lower()
                if measurement_key not in self.data:
                    self.data[measurement_key] = []
                    self.data["time"] = []
                
                for i, value in enumerate(values):
                    calculated_time = base_time + (i * 0.01)
                    self.data["time"].append(calculated_time)
                    self.data[measurement_key].append(value)
                
                # Update the UI to show we have new data
                self.update_checkboxes()
                self.show_recent()
            else:
                # Handle the case of a single value (not in a list)
                try:
                    # Clean up the data regardless of format
                    cleaned_data = self.clean_data_string(full_data)
                    
                    # Parse the data values
                    values = self.parse_numeric_values(cleaned_data)
                    
                    if values:  # Make sure we have values to process
                        # Use fixed time interval of 10ms between measurements
                        base_time = time.time()  # Current time as base
                        
                        # Add the measurement data with the current measurement type as the key
                        measurement_key = self.current_measurement_type.lower()
                        
                        # Initialize data arrays if needed
                        if measurement_key not in self.data:
                            self.data[measurement_key] = []
                        
                        # Ensure time array exists
                        if "time" not in self.data:
                            self.data["time"] = []
                        
                        # Add each value with a calculated timestamp (10ms intervals)
                        for i, value in enumerate(values):
                            calculated_time = base_time + (i * 0.01)  # 10ms = 0.01 seconds
                            self.data["time"].append(calculated_time)
                            self.data[measurement_key].append(value)
                        
                        # Update the UI to show we have new data
                        self.update_checkboxes()
                        self.show_recent()
                    else:
                        self.data_label.setText("No valid numeric values found in the received data")
                except ValueError:
                    self.data_label.setText(f"Received data is not in the expected format: {full_data}")
        
        except Exception as e:
            self.data_label.setText(f"Error processing data: {str(e)}")
    
    def clean_data_string(self, data_string):
        """Clean up data string with extra spaces or commas."""
        # Remove the brackets
        data_string = data_string.strip()
        if data_string.startswith('['):
            data_string = data_string[1:]
        if data_string.endswith(']'):
            data_string = data_string[:-1]
        
        # Replace multiple spaces with a single space
        data_string = re.sub(r'\s+', ' ', data_string)
        
        # Replace multiple commas with a single comma
        data_string = re.sub(r',+', ',', data_string)
        
        # Replace space comma with just comma
        data_string = re.sub(r'\s*,\s*', ',', data_string)
        
        # Replace space with comma to standardize
        data_string = re.sub(r'\s+', ',', data_string)
        
        # Remove any commas at the start or end
        data_string = data_string.strip(',')
        
        return data_string

    def parse_numeric_values(self, cleaned_data):
        """Parse numeric values from a cleaned data string."""
        values = []
        if not cleaned_data:
            return values
        
        # Split by comma
        parts = cleaned_data.split(',')
        
        # Convert each part to a float if possible
        for part in parts:
            part = part.strip()
            if part:
                try:
                    values.append(float(part))
                except ValueError:
                    print(f"Warning: could not convert '{part}' to float")
        
        return values

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
        self.battery_number.setRange(1, 2)
        self.battery_number.setValue(1)
        command_layout.addRow("Battery #:", self.battery_number)
        
        # Measurement type dropdown
        self.measurement_type = QComboBox()
        self.measurement_type.addItems(["Voltage", "Power", "Current", "Life"])
        command_layout.addRow("Measurement:", self.measurement_type)
        
        # Measurement count
        self.measurement_count = QSpinBox()
        self.measurement_count.setRange(1, 300)
        self.measurement_count.setValue(100)
        command_layout.addRow("# of Measurements:", self.measurement_count)
        
        command_group.setLayout(command_layout)
        layout.addWidget(command_group)
        
        # Request button
        self.request_button = QPushButton("Request Data from Battery")
        self.request_button.clicked.connect(self.send_request)
        self.request_button.setEnabled(False)  # Disabled until connected
        self.addGlowEffect(self.request_button)
        layout.addWidget(self.request_button)
        
        # Load data button
        self.load_button = QPushButton("Load Battery Data")
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
        self.checkbox_container.setStyleSheet("background-color: #FFFFFF;")
        self.checkbox_layout = QVBoxLayout(self.checkbox_container)
        self.scroll_area.setWidget(self.checkbox_container)
        layout.addWidget(self.scroll_area)

        # Buttons to show data
        button_layout = QHBoxLayout()

        self.show_recent_button = QPushButton("Show Latest Data")
        self.show_recent_button.clicked.connect(self.show_recent)
        self.addGlowEffect(self.show_recent_button)
        button_layout.addWidget(self.show_recent_button)

        self.plot_button = QPushButton("Plot Data Over Time")
        self.plot_button.clicked.connect(self.plot_data)
        self.addGlowEffect(self.plot_button)
        button_layout.addWidget(self.plot_button)

        self.export_button = QPushButton("Export Data")
        self.export_button.clicked.connect(self.export_debug_json)
        self.addGlowEffect(self.export_button)
        button_layout.addWidget(self.export_button)

        layout.addLayout(button_layout)

        self.setLayout(layout)
        self.setWindowTitle("Battery Monitoring System")
        self.setGeometry(100, 100, 600, 550)  # Increased height for new controls

        # Create a simple icon for the window
        icon_pixmap = QPixmap(32, 32)
        icon_pixmap.fill(QColor("#0D6EFD"))
        self.setWindowIcon(QIcon(icon_pixmap))

        self.setBackgroundImage()

    def addGlowEffect(self, widget):
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(8)
        shadow.setColor(QColor("#B0BEC5"))
        shadow.setOffset(0, 2)
        widget.setGraphicsEffect(shadow)

    def setBackgroundImage(self):
        self.setStyleSheet("background-color: #F2EBCC;")

    def resizeEvent(self, event):
        self.setBackgroundImage()
        super().resizeEvent(event)

    def applyStyles(self):
        self.setStyleSheet("""
            QWidget {
                background-color: #FAFAFA;
                color: #212121;
                selection-background-color: #1976D2;
                selection-color: #FFFFFF;  
            }
            QPushButton {
                background-color: #CDE5D9;
                border: 1px solid #90CAF9;
                padding: 8px 12px;
                border-radius: 6px;
                font-weight: bold;
                font-size: 13px;
                color: #0D47A1;
            }
            QPushButton:hover {
            background-color: #E3F2FD;
            border: 1px solid #1976D2;
            }
            QPushButton:disabled {
                background-color: #E0E0E0;
                border-color: #BDBDBD;
                color: #9E9E9E;
            }
            QLabel {
                font-size: 13px;
                font-weight: bold;
                color: #212121;
                background-color: #FFFFFF;
                padding: 8px;
                border-radius: 6px;
                border: 1px solid #E0E0E0;
            }
            QCheckBox {
                font-size: 13px;
                padding: 5px;
                color: #212121;
                background-color: transparent;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
            }
            QProgressBar {
                border: 1px solid #90CAF9;
                border-radius: 4px;
                text-align: center;
                background-color: #E3F2FD;
                height: 10px;
            }
            QProgressBar::chunk {
                background-color: #1976D2;
            }
            QGroupBox {
                font-size: 13px;
                font-weight: bold;
                border: 1px solid #90CAF9;
                border-radius: 6px;
                margin-top: 1ex;
                padding: 10px;
                background-color: #FFFFFF;
                color: #212121;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top center;
                padding: 0 5px;
                color: #1565C0;
            }
            QComboBox {
                border: 1px solid #B0BEC5;
                border-radius: 4px;
                padding: 6px 10px;
                background-color: #FFFFFF;
                color: #212121;
                min-height: 28px;
                min-width: 160px;  /* Make measurement field wider */
                selection-background-color: #1976D2;
                selection-color: #FFFFFF;
            }
            QSpinBox {
                border: 1px solid #B0BEC5;
                border-radius: 4px;
                padding: 6px 8px;
                background-color: #FFFFFF;
                color: #212121;
                min-height: 28px;
            }
            QScrollArea {
                border: 1px solid #CFD8DC;
                border-radius: 6px;
                background-color: #FFFFFF;
            }
            QLineEdit {
                border: 1px solid #B0BEC5;
                border-radius: 4px;
                padding: 6px 8px;
                background-color: #FFFFFF;
                color: #212121;
                selection-background-color: #1976D2;
                selection-color: #FFFFFF;
                font-weight: bold;
            }
        """)

        font = QFont("Segoe UI", 11)
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
            self.data_label.setText("No parameter selected.")
            return

        if not self.data.get("time"):
            self.data_label.setText("No data available to display.")
            return

        # Find the most recent data point for each selected key
        display_text = "Latest Battery Data:\n"
        
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
            self.data_label.setText("No parameter selected for plotting.")
            return

        if not self.data.get("time") or len(self.data["time"]) == 0:
            self.data_label.setText("No time data available for plotting.")
            return

        plt.figure(figsize=(10, 6))
        plt.style.use('ggplot')
        
        for key in self.selected_keys:
            if key in self.data and len(self.data[key]) > 0:
                if len(self.data["time"]) > 0:
                    start_time = self.data["time"][0]
                    relative_times = [(t - start_time) / 60 for t in self.data["time"]]
                    
                    data_points = self.data[key][:len(relative_times)]
                    
                    plt.plot(relative_times, data_points, label=key, marker='o', linewidth=2)

        plt.xlabel("Time (minutes)", fontsize=12)
        
        if any("voltage" in k.lower() for k in self.selected_keys):
            plt.ylabel("Voltage (V)", fontsize=12)
        elif any("current" in k.lower() for k in self.selected_keys):
            plt.ylabel("Current (A)", fontsize=12)
        elif any("power" in k.lower() for k in self.selected_keys):
            plt.ylabel("Power (W)", fontsize=12)
        elif any("life" in k.lower() for k in self.selected_keys):
            plt.ylabel("Battery Life (%)", fontsize=12)
        else:
            plt.ylabel("Values", fontsize=12)
            
        plt.title("Battery Measurements Over Time", fontsize=14)
        plt.legend(frameon=True)
        plt.grid(True, alpha=0.3)
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