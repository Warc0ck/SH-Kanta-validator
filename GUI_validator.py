import requests
import json
import xml.etree.ElementTree as ET
import re
import tkinter as tk
from tkinter import filedialog, messagebox

# Function to save response to file
def save_to_file(filename, content):
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(content)

# Function to display result in a new window 
def show_result(title, content, window_size="800x600"): 
    result_window = tk.Toplevel() 
    result_window.title(title) 
    result_window.geometry(window_size) 
    text_widget = tk.Text(result_window, wrap="word") 
    text_widget.insert("1.0", content) 
    text_widget.pack(expand=1, fill="both")

# Main function to handle the script logic
def main():
    # API endpoint
    url = "http://shvalidaattori.at.kanta.fi/shark-validointi/validoi/asiakirja/tulos"

    # File dialog to select XML files
    kehys_file = filedialog.askopenfilename(title="Valitse Interface message xml-tiedosto", filetypes=[("XML files", "*.xml")])
    asiakirja_file = filedialog.askopenfilename(title="Valitse SourceXML.xml tiedosto", filetypes=[("XML files", "*.xml")])

    # Reading the XML files
    with open(kehys_file, 'r') as file1:
        siirtokehysXml = file1.read()

    with open(asiakirja_file, 'r') as file2:
        asiakirjaXml = file2.read()

    # Registering namespaces
    namespaces = {'': 'urn:hl7-org:v3'}  # The default namespace

    # Parsing the siirtokehysXml to find the value of the reasonCode attribute
    try:
        root = ET.fromstring(siirtokehysXml)
        print("Root element:", root.tag)  # Debugging: Print root element

        # Find the controlActProcess element
        control_act_process_element = root.find('.//{urn:hl7-org:v3}controlActProcess', namespaces)
        print("controlActProcess element found:", control_act_process_element is not None)  # Debugging

        if control_act_process_element is not None:
            # Find the reasonCode element within controlActProcess
            reason_code_element = control_act_process_element.find('{urn:hl7-org:v3}reasonCode')
            print("reasonCode element found:", reason_code_element is not None)  # Debugging

            if reason_code_element is not None:
                reason_code = reason_code_element.get('code')
                print("Palvelupyyntö: ", reason_code)
            else:
                print("reasonCode element not found. Check the XML structure and XPath expression.")
        else:
            print("controlActProcess element not found. Check the XML structure and XPath expression.")
    except ET.ParseError as e:
        messagebox.showerror("Virhe", f"Palvelupyyntö koodia ei löydy: {e}")
        return

    # Data to be sent
    data = {
        "messageId": "1234567890",
        "palveluPyynto": reason_code,
        "level": "1",
        "siirtokehysXml": siirtokehysXml,
        "asiakirjaXml": asiakirjaXml
    }

    # Headers
    headers = {
        "Content-Type": "application/json",
    }

    # Sending POST request
    response = requests.post(url, json=data, headers=headers)

    # Checking if the request was successful
    if response.status_code == 200:
        response_json = response.json()
        # Pretty print the response
        formatted_response = json.dumps(response_json, indent=4, ensure_ascii=False)
        print("Request successful:", formatted_response)

        # Save the response to a file
        save_to_file('response.json', formatted_response)

        # Format the description
        if 'description' in response_json:
            formatted_description = re.sub(r'(?<=\d:)', '\n', response_json['description'])
            formatted_description = formatted_description.replace(';', ';\n')
            print("Formatted description:\n", formatted_description)
            save_to_file('formatted_description.txt', formatted_description)
            messagebox.showinfo("Success", f"Request successful:\n{formatted_response}")
    else:
        # Pretty print the error response
        try:
            error_response = response.json()
            formatted_error = json.dumps(error_response, indent=4, ensure_ascii=False)
            print("Error in request:", response.status_code, formatted_error)

            # Save the error response to a file
            save_to_file('error_response.json', formatted_error)

            # Format the error description
            if 'description' in error_response:
                formatted_description = re.sub(r'(?<=\d:)', '\n', error_response['description'])
                formatted_description = formatted_description.replace(';', ';\n')
                print("Formatted error description:\n", formatted_description)
                save_to_file('formatted_error_description.txt', formatted_description)
            messagebox.showerror("Error", f"Error in request:\n{formatted_error}")
        except json.JSONDecodeError:
            print("Error in request:", response.status_code, response.text)
            save_to_file('error_response.txt', response.text)
            messagebox.showerror("Error", f"Error in request:\n{response.text}")

# Create the main window
root = tk.Tk()
root.title("SOSH Kanta validointityökalu")
root.geometry("400x200")

# Add a label with instructions 
instructions = tk.Label(root, text="Valitse XML-tiedostot ja paina Käynnistä aloittaaksesi validoinnin.", font=("Helvetica", 14))
instructions.pack(pady=20)

# Add a button to run the main script
run_button = tk.Button(root, text="Käynnistä", command=main)
run_button.pack(pady=20)

# Start the Tkinter event loop
root.mainloop()
