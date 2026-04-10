import dearpygui.dearpygui as dpg
import math
import time

# --- Configuration ---
CANVAS_WIDTH = 600
CANVAS_HEIGHT = 600
WINDOW_WIDTH = CANVAS_WIDTH + 50
WINDOW_HEIGHT = CANVAS_HEIGHT + 150
CIRCLE_RADIUS = 250
LINE_COLOR = (255, 255, 0, 150) # Yellow, semi-transparent
HEAD_COLOR = (0, 255, 255, 255) # Cyan
INITIAL_A = 3.0
INITIAL_B = 4.0
INITIAL_SPEED = 1.0

# --- Application State ---
t = 0.0
last_pos = None

# --- Main Logic ---

def create_lissajous_point(a, b, delta, speed):
    """Calculates a point on the Lissajous curve."""
    global t, last_pos
    
    # Increment time based on speed
    t += 0.01 * speed
    
    # Parametric equations for the Lissajous curve
    x = CIRCLE_RADIUS * math.sin(a * t + delta) + (CANVAS_WIDTH / 2)
    y = CIRCLE_RADIUS * math.sin(b * t) + (CANVAS_HEIGHT / 2)
    
    current_pos = (x, y)
    
    # Draw the line segment from the last point to the current point
    if last_pos:
        dpg.draw_line(p1=last_pos, p2=current_pos, color=LINE_COLOR, thickness=1, parent="canvas_drawlist")
    
    # Draw a circle at the head of the curve
    dpg.draw_circle(center=current_pos, radius=3, color=HEAD_COLOR, fill=HEAD_COLOR, parent="head_drawlist")
    
    last_pos = current_pos

def clear_drawing():
    """Clears the drawing canvas and resets the animation state."""
    global t, last_pos
    t = 0.0
    last_pos = None
    dpg.delete_item("canvas_drawlist", children_only=True)
    dpg.delete_item("head_drawlist", children_only=True)

def update_animation(sender, app_data, user_data):
    """The main render callback, called every frame."""
    a = dpg.get_value("param_a")
    b = dpg.get_value("param_b")
    speed = dpg.get_value("param_speed")
    delta = math.pi / 2 # Phase shift, can also be a slider
    
    # Clear the head circle from the previous frame
    dpg.delete_item("head_drawlist", children_only=True)
    
    # Draw the next point/line segment
    create_lissajous_point(a, b, delta, speed)

# --- GUI Setup ---

dpg.create_context()
dpg.create_viewport(title='Lissajous Curve Animation', width=WINDOW_WIDTH, height=WINDOW_HEIGHT)

with dpg.window(label="Animation Canvas", width=CANVAS_WIDTH, height=CANVAS_HEIGHT, no_move=True, no_resize=True) as canvas_window:
    with dpg.drawlist(width=CANVAS_WIDTH, height=CANVAS_HEIGHT):
        # We use two drawlists to easily clear the moving "head" without clearing the main trail
        dpg.add_draw_node(tag="canvas_drawlist") # For the persistent line trail
        dpg.add_draw_node(tag="head_drawlist")   # For the moving head circle

with dpg.window(label="Controls", width=CANVAS_WIDTH, height=100, pos=(0, CANVAS_HEIGHT)):
    with dpg.group(horizontal=True):
        dpg.add_slider_float(label="a", default_value=INITIAL_A, min_value=1.0, max_value=10.0, tag="param_a", width=150)
        dpg.add_slider_float(label="b", default_value=INITIAL_B, min_value=1.0, max_value=10.0, tag="param_b", width=150)
        dpg.add_slider_float(label="Speed", default_value=INITIAL_SPEED, min_value=0.1, max_value=5.0, tag="param_speed", width=150)
        dpg.add_button(label="Clear Canvas", callback=clear_drawing)

dpg.setup_dearpygui()
dpg.show_viewport()

while dpg.is_dearpygui_running():
    update_animation(None, None, None)
    dpg.render_dearpygui_frame()

dpg.destroy_context()
