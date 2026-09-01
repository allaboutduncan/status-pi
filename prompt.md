I have a raspberry pi zero 2 w
and a waveshare RPI 3.5 LCD

I want to build an app that will mimic the features of Busy Bar (https://busy.app/) as much as possible
This will be a self contained screen with no buttons or keyboard input. It should use the API to retrieve all information and display it on the screen. It should use the native API if possible to reduce CPU usage. The device will always be connected to power and WiFi

Resolution 480x320 is default and landscape orientation

The screen will be mounted to a wall and will be in landscape mode.

I want it to display the following information: 
- Current Time and Date
- Connect to by Google Calendar via URL to show upcoming meetings I have accepted
- Show "BUSY" in large text if I am in a meeting (my microphone is active)
  - for microphone status we will use Home Assistant. I cannot install items on my work computer but I currently have an automation that uses the Camera On status to change the color of a light bulb - we can check the automation details for more information. 
- Show "FREE" in large text if I am not in a meeting
- If "BUSY" is showing, it should show the title of the meeting
- If "FREE" is showing, it should show the title and time of the next meeting
- UI should be accessible via Web/URL to allow me to change/update/reconfigure the device. 
- Ability to set a timer and countdown via Web UI