import sys
import os
import json
import matplotlib.pyplot as plt
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QPushButton, QLabel, QCheckBox, QFileDialog, QHBoxLayout, QScrollArea
)
from PyQt5.QtGui import QFont, QIcon, QPixmap, QPalette, QBrush
from PyQt5.QtCore import Qt


class BatteryDataViewer(QWidget):
    def __init__(self):
        super().__init__()

        self.data = {}  # Dictionary to store parsed JSON data
        self.timestamps = []  # List to store time points
        self.selected_keys = []  # User-selected parameters

        self.initUI()
        self.applyStyles()

    def initUI(self):
        """Initialize the GUI layout and components."""
        layout = QVBoxLayout()

        # Load Data Button
        self.load_button = QPushButton("📂 Load Battery Data")
        self.load_button.clicked.connect(self.load_data)
        layout.addWidget(self.load_button)

        # Display Loaded Data Info
        self.data_label = QLabel("No data loaded.")
        self.data_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.data_label)

        # Scrollable Checkbox Layout (For many parameters)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.checkbox_container = QWidget()
        self.checkbox_layout = QVBoxLayout(self.checkbox_container)
        self.scroll_area.setWidget(self.checkbox_container)
        layout.addWidget(self.scroll_area)

        # Buttons to show data
        button_layout = QHBoxLayout()

        self.show_recent_button = QPushButton("📊 Show Instantaneous Data")
        self.show_recent_button.clicked.connect(self.show_recent)
        button_layout.addWidget(self.show_recent_button)

        self.plot_button = QPushButton("📈 Plot Data Over Time")
        self.plot_button.clicked.connect(self.plot_data)
        button_layout.addWidget(self.plot_button)

        layout.addLayout(button_layout)

        self.setLayout(layout)
        self.setWindowTitle("🚀 Rocket Battery Monitoring System")
        self.setGeometry(100, 100, 600, 400)

        # Hardcoded window icon (Rocket Emoji Icon)
        self.setWindowIcon(QIcon(QPixmap(32, 32)))

        # Resize background image
        self.setBackgroundImage()

    def setBackgroundImage(self):
        """Resize and set the background image while maintaining aspect ratio."""
        image_path = os.path.abspath("images/background.jpg")  # Path to background image

        if os.path.exists(image_path):
            pixmap = QPixmap(image_path)

            # Resize the image to fit the widget while keeping the aspect ratio
            scaled_pixmap = pixmap.scaled(
                self.size(),  # Scale to widget size
                Qt.KeepAspectRatioByExpanding,  # Keep aspect ratio while covering
                Qt.SmoothTransformation  # Smooth scaling
            )

            palette = self.palette()
            palette.setBrush(QPalette.Background, QBrush(scaled_pixmap))
            self.setPalette(palette)

    def resizeEvent(self, event):
        """Ensure background image resizes proportionally when the window is resized."""
        self.setBackgroundImage()
        super().resizeEvent(event)

    def applyStyles(self):
        """Apply a professional aerospace-themed style."""
        self.setStyleSheet("""
            QPushButton {
                background-color: rgba(58, 63, 68, 200);
                border: 2px solid #61AFEF;
                padding: 8px;
                border-radius: 5px;
                font-weight: bold;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #61AFEF;
                color: black;
            }
            QLabel {
                font-size: 16px;
                font-weight: bold;
                color: #D8DEE9;
                background-color: rgba(30, 30, 30, 180);
                padding: 5px;
                border-radius: 5px;
            }
            QCheckBox {
                font-size: 14px;
                padding: 3px;
                background-color: rgba(30, 30, 30, 180);
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
            }
        """)

        # Custom font settings
        font = QFont("Arial", 12)
        self.data_label.setFont(font)
        self.load_button.setFont(QFont("Arial", 12, QFont.Bold))

    def load_data(self):
        """Load JSON data from the data/ folder."""
        options = QFileDialog.Options()
        file_name, _ = QFileDialog.getOpenFileName(self, "Open JSON File", "data/", "JSON Files (*.json);;All Files (*)", options=options)
        
        if file_name:
            with open(file_name, 'r') as file:
                self.data = json.load(file)

            self.timestamps = self.data.get("time", [])
            self.update_checkboxes()

    def update_checkboxes(self):
        """Create checkboxes for available data keys."""
        # Clear old checkboxes
        for i in reversed(range(self.checkbox_layout.count())):
            widget = self.checkbox_layout.itemAt(i).widget()
            if widget is not None:
                widget.setParent(None)

        # Create new checkboxes
        self.checkboxes = {}
        for key in self.data.keys():
            if key != "time":  # Skip timestamps
                checkbox = QCheckBox(key)
                self.checkbox_layout.addWidget(checkbox)
                self.checkboxes[key] = checkbox

        self.data_label.setText("✅ Data Loaded. Select parameters to display.")

    def get_selected_keys(self):
        """Get the list of selected parameters."""
        self.selected_keys = [key for key, checkbox in self.checkboxes.items() if checkbox.isChecked()]

    def show_recent(self):
        """Display the most recent values of selected parameters."""
        self.get_selected_keys()
        if not self.selected_keys:
            self.data_label.setText("⚠️ No parameter selected.")
            return

        last_index = -1  # Last time point
        recent_values = {key: self.data[key][last_index] for key in self.selected_keys if key in self.data}

        display_text = "📊 Instantaneous Data:\n" + "\n".join([f"{key}: {value}" for key, value in recent_values.items()])
        self.data_label.setText(display_text)

    def plot_data(self):
        """Plot selected parameters over time."""
        self.get_selected_keys()
        if not self.selected_keys:
            self.data_label.setText("⚠️ No parameter selected for plotting.")
            return

        plt.figure(figsize=(10, 6))
        for key in self.selected_keys:
            if key in self.data:
                plt.plot(self.timestamps, self.data[key], label=key)

        plt.xlabel("Time")
        plt.ylabel("Values")
        plt.title("Battery System Data Over Time")
        plt.legend()
        plt.grid(True)
        plt.show()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    viewer = BatteryDataViewer()
    viewer.show()
    sys.exit(app.exec_())
