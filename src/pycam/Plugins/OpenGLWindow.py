# -*- coding: utf-8 -*-
"""
$Id$

Copyright 2011 Lars Kruse <devel@sumpfralle.de>

This file is part of PyCAM.

PyCAM is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

PyCAM is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with PyCAM.  If not, see <http://www.gnu.org/licenses/>.
"""

# careful import
try:
    import gtk.gtkgl
    import OpenGL.GL as GL
    import OpenGL.GLU as GLU
    import OpenGL.GLUT as GLUT
    import pango
    GL_ENABLED = True
except (ImportError, RuntimeError):
    GL_ENABLED = False

import gtk
import math

from pycam.Gui.OpenGLTools import draw_complete_model_view
from pycam.Geometry.Point import Point
import pycam.Geometry.Matrix as Matrix
from pycam.Geometry.utils import sqrt, number
import pycam.Plugins


# The length of the distance vector does not matter - it will be normalized and
# multiplied later anyway.
VIEWS = {
    "reset": {"distance": (-1.0, -1.0, 1.0), "center": (0.0, 0.0, 0.0),
            "up": (0.0, 0.0, 1.0), "znear": 0.01, "zfar": 10000.0, "fovy": 30.0},
    "top": {"distance": (0.0, 0.0, 1.0), "center": (0.0, 0.0, 0.0),
            "up": (0.0, 1.0, 0.0), "znear": 0.01, "zfar": 10000.0, "fovy": 30.0},
    "bottom": {"distance": (0.0, 0.0, -1.0), "center": (0.0, 0.0, 0.0),
            "up": (0.0, 1.0, 0.0), "znear": 0.01, "zfar": 10000.0, "fovy": 30.0},
    "left": {"distance": (-1.0, 0.0, 0.0), "center": (0.0, 0.0, 0.0),
            "up": (0.0, 0.0, 1.0), "znear": 0.01, "zfar": 10000.0, "fovy": 30.0},
    "right": {"distance": (1.0, 0.0, 0.0), "center": (0.0, 0.0, 0.0),
            "up": (0.0, 0.0, 1.0), "znear": 0.01, "zfar": 10000.0, "fovy": 30.0},
    "front": {"distance": (0.0, -1.0, 0.0), "center": (0.0, 0.0, 0.0),
            "up": (0.0, 0.0, 1.0), "znear": 0.01, "zfar": 10000.0, "fovy": 30.0},
    "back": {"distance": (0.0, 1.0, 0.0), "center": (0.0, 0.0, 0.0),
            "up": (0.0, 0.0, 1.0), "znear": 0.01, "zfar": 10000.0, "fovy": 30.0},
}

# buttons for rotating, moving and zooming the model view window
BUTTON_ROTATE = gtk.gdk.BUTTON1_MASK
BUTTON_MOVE = gtk.gdk.BUTTON2_MASK
BUTTON_ZOOM = gtk.gdk.BUTTON3_MASK
BUTTON_RIGHT = 3

# floating point color values are only available since gtk 2.16
GTK_COLOR_MAX = 65535.0


class OpenGLWindow(pycam.Plugins.PluginBase):

    UI_FILE = "opengl.ui"
    # TODO: drop the dependecy on "Models" as soon as the calls for get("models") are gone
    DEPENDS = ["Models"]
    CATEGORIES = ["Visualization", "OpenGL"]

    def setup(self):
        if not GL_ENABLED:
            self.log.error("Failed to initialize the interactive 3D model view."
                    + "\nPlease install 'python-gtkglext1' to enable it.")
            return False
        if self.gui:
            self.window = self.gui.get_object("OpenGLWindow")
            self.core.get("configure-drag-drop-func")(self.window)
            accel_group = self.core.get("gtk-accel-group")
            if not accel_group is None:
                self.window.add_accel_group(accel_group)
            self.initialized = False
            self.busy = False
            self.is_visible = False
            self._last_view = VIEWS["reset"]
            self._position = [200, 200]
            box = self.gui.get_object("OpenGLPrefTab")
            self.core.register_ui("preferences", "OpenGL", box, 40)
            self._gtk_handlers = []
            # options
            # TODO: move the default value somewhere else
            for name, objname, default in (
                    ("view_light", "OpenGLLight", True),
                    ("view_shadow", "OpenGLShadow", True),
                    ("view_polygon", "OpenGLPolygon", True),
                    ("view_perspective", "OpenGLPerspective", True)):
                obj = self.gui.get_object(objname)
                self.core.add_item(name, obj.get_active, obj.set_active)
                obj.set_active(default)
                self._gtk_handlers.append((obj, "toggled", self.glsetup))
                self._gtk_handlers.append((obj, "toggled",
                        "visual-item-updated"))
            # frames per second
            skip_obj = self.gui.get_object("DrillProgressFrameSkipControl")
            self.core.add_item("drill_progress_max_fps",
                    skip_obj.get_value, skip_obj.set_value)
            # color box
            color_frame = self.gui.get_object("ColorPrefTab")
            color_frame.unparent()
            self._color_settings = {}
            self.core.register_ui("preferences", "Colors", color_frame, 30)
            self.core.set("register_color", self.register_color_setting)
            self.core.set("unregister_color", self.unregister_color_setting)
            # TODO: move the "tool" color to a separate plugin
            # TODO: move "material" to simulation viewer
            # TODO: move "support grid" to support grid visualization
            for name, label, weight in (
                    ("color_background", "Background", 10),
                    ("color_model", "Model", 20),
                    ("color_support_grid", "Support grid", 30),
                    ("color_cutter", "Tool", 50),
                    ("color_material", "Material", 80)):
                self.core.get("register_color")(name, label, weight)
            # display items
            items_frame = self.gui.get_object("DisplayItemsPrefTab")
            items_frame.unparent()
            self._display_items = {}
            self.core.register_ui("preferences", "Display Items", items_frame,
                    20)
            self.core.set("register_display_item", self.register_display_item)
            self.core.set("unregister_display_item",
                    self.unregister_display_item)
            # visual and general settings
            # TODO: move support grid and drill to a separate plugin
            for name, label, weight in (
                    ("show_support_grid", "Show Support Grid", 20),
                    ("show_dimensions", "Show Dimensions", 60),
                    ("show_drill", "Show Tool", 70),
                    ("show_directions", "Show Directions", 80)):
                self.core.get("register_display_item")(name, label, weight)
            # toggle window state
            toggle_3d = self.gui.get_object("Toggle3DView")
            self._gtk_handlers.append((toggle_3d, "toggled",
                    self.toggle_3d_view))
            self.register_gtk_accelerator("opengl", toggle_3d,
                    "<Control><Shift>v", "ToggleOpenGLView")
            self.core.register_ui("view_menu", "ViewOpenGL", toggle_3d, -20)
            self.mouse = {"start_pos": None, "button": None,
                    "event_timestamp": 0, "last_timestamp": 0,
                    "pressed_pos": None, "pressed_timestamp": 0,
                    "pressed_button": None}
            self.window.connect("delete-event", self.destroy)
            self.window.set_default_size(560, 400)
            self.container = self.gui.get_object("OpenGLBox")
            for obj_name, view in (("ResetView", "reset"),
                    ("LeftView", "left"), ("RightView", "right"),
                    ("FrontView", "front"), ("BackView", "back"),
                    ("TopView", "top"), ("BottomView", "bottom")):
                kwargs = {"view": view}
                self._gtk_handlers.append((self.gui.get_object(obj_name),
                           "clicked", self.rotate_view, VIEWS[view]))
            # key binding
            self._gtk_handlers.append((self.window, "key-press-event",
                    self.key_handler))
            # OpenGL stuff
            glconfig = gtk.gdkgl.Config(mode=gtk.gdkgl.MODE_RGBA \
                    | gtk.gdkgl.MODE_DEPTH | gtk.gdkgl.MODE_DOUBLE)
            self.area = gtk.gtkgl.DrawingArea(glconfig)
            # first run; might also be important when doing other fancy
            # gtk/gdk stuff
            #self.area.connect_after('realize', self.paint)
            # called when a part of the screen is uncovered
            self._gtk_handlers.append((self.area, 'expose-event', self.paint))
            # resize window
            self._gtk_handlers.append((self.area, 'configure-event',
                    self._resize_window))
            # catch mouse events
            self.area.set_events(gtk.gdk.MOUSE | gtk.gdk.POINTER_MOTION_HINT_MASK \
                    | gtk.gdk.BUTTON_PRESS_MASK | gtk.gdk.BUTTON_RELEASE_MASK \
                    | gtk.gdk.SCROLL_MASK)
            self._gtk_handlers.extend((
                    (self.area, "button-press-event", self.mouse_press_handler),
                    (self.area, "motion-notify-event", self.mouse_handler),
                    (self.area, "button-release-event", self.context_menu_handler),
                    (self.area, "scroll-event", self.scroll_handler)))
            self.container.pack_end(self.area)
            self.camera = Camera(self.core, lambda: (self.area.allocation.width,
                    self.area.allocation.height))
            # Color the dimension value according to the axes.
            # For "y" axis: 100% green is too bright on light background - we
            # reduce it a bit.
            for color, names in (
                    (pango.AttrForeground(65535, 0, 0, 0, 100),
                            ("model_dim_x_label", "model_dim_x", "ModelCornerXMax",
                                "ModelCornerXMin", "ModelCornerXSpaces")),
                    (pango.AttrForeground(0, 50000, 0, 0, 100),
                            ("model_dim_y_label", "model_dim_y", "ModelCornerYMax",
                                "ModelCornerYMin", "ModelCornerYSpaces")),
                    (pango.AttrForeground(0, 0, 65535, 0, 100),
                            ("model_dim_z_label", "model_dim_z", "ModelCornerZMax",
                                "ModelCornerZMin", "ModelCornerZSpaces"))):
                attributes = pango.AttrList()
                attributes.insert(color)
                for name in names:
                    self.gui.get_object(name).set_attributes(attributes)
            # add the items buttons (for configuring visible items)
            # TODO: enable the context menu
            if False and item_buttons:
                items_button_container = self.gui.get_object("ViewItems")
                for button in item_buttons:
                    new_checkbox = gtk.ToggleToolButton()
                    new_checkbox.set_label(button.get_label())
                    new_checkbox.set_active(button.get_active())
                    # Configure the two buttons (in "Preferences" and in the 3D view
                    # widget) to toggle each other. This is required for a
                    # consistent view of the setting.
                    connect_button_handlers("toggled", button, new_checkbox)
                    items_button_container.insert(new_checkbox, -1)
                items_button_container.show_all()
                # create the drop-down menu
                if context_menu_actions:
                    action_group = gtk.ActionGroup("context")
                    for action in context_menu_actions:
                        action_group.add_action(action)
                    uimanager = gtk.UIManager()
                    # The "pos" parameter is optional since gtk 2.12 - we can
                    # remove it later.
                    uimanager.insert_action_group(action_group, pos=-1)
                    uimanager_template = '<ui><popup name="context">%s</popup></ui>'
                    uimanager_item_template = """<menuitem action="%s" />"""
                    uimanager_text = uimanager_template % "".join(
                            [uimanager_item_template % action.get_name()
                                    for action in context_menu_actions])
                    uimanager.add_ui_from_string(uimanager_text)
                    self.context_menu = uimanager.get_widget("/context")
                    self.context_menu.insert(gtk.SeparatorMenuItem(), 0)
                else:
                    self.context_menu = gtk.Menu()
                for index, button in enumerate(item_buttons):
                    new_item = gtk.CheckMenuItem(button.get_label())
                    new_item.set_active(button.get_active())
                    connect_button_handlers("toggled", button, new_item)
                    self.context_menu.insert(new_item, index)
                self.context_menu.show_all()
            else:
                self.context_menu = None
            self._event_handlers = (
                    ("model-change-after", self.update_model_dimensions),
                    ("visual-item-updated", self.update_model_dimensions),
                    ("visual-item-updated", self.update_view),
                    ("visualization-state-changed", self._update_widgets),
                    ("model-list-changed", self._restore_latest_view))
            # handlers
            self.register_gtk_handlers(self._gtk_handlers)
            self.register_event_handlers(self._event_handlers)
            # show the window - the handlers _must_ be registered before "show"
            self.area.show()
            self.container.show()
            toggle_3d.set_active(True)
            # refresh display
            self.core.emit_event("visual-item-updated")
        return True

    def teardown(self):
        if self.gui:
            toggle_3d = self.gui.get_object("Toggle3DView")
            # hide the window
            toggle_3d.set_active(False)
            self.core.unregister_ui("view_menu", toggle_3d)
            self.unregister_gtk_accelerator("opengl", toggle_3d)
            self.core.unregister_ui("view_menu", toggle_3d)
            for name in ("color_background", "color_model",
                    "color_support_grid", "color_cutter", "color_material"):
                self.core.get("unregister_color")(name)
            for name in ("show_support_grid", "show_dimensions", "show_drill",
                    "show_directions"):
                self.core.get("unregister_display_item")(name)
            self.unregister_gtk_handlers(self._gtk_handlers)
            self.unregister_event_handlers(self._event_handlers)
            # the area will be created during setup again
            self.container.remove(self.area)
            self.area = None

    def update_view(self, widget=None, data=None):
        if self.is_visible:
            self.paint()

    def _update_widgets(self):
        self.unregister_gtk_handlers(self._gtk_handlers)
        self.gui.get_object("Toggle3DView").set_active(self.is_visible)
        self.register_gtk_handlers(self._gtk_handlers)

    def update_model_dimensions(self, widget=None):
        models = self.core.get("models").get_visible()
        low, high = pycam.Geometry.Model.get_combined_bounds(models)
        if None in low or None in high:
            low, high = (0, 0, 0), (0, 0, 0)
        # model corners in 3D view
        if None in low or None in high:
            # all models are empty
            return
        for value, label_suffix in ((low[0], "XMin"), (low[1], "YMin"),
                (low[2], "ZMin"), (high[0], "XMax"), (high[1], "YMax"),
                (high[2], "ZMax")):
            label_name = "ModelCorner%s" % label_suffix
            value = "%.3f" % value
            self.gui.get_object(label_name).set_label(value)

    def register_display_item(self, name, label, weight=100):
        if name in self._display_items:
            self.log.debug("Tried to register display item '%s' twice" % name)
            return
        widget = gtk.CheckButton(label)
        widget.connect("toggled", lambda widget: \
                self.core.emit_event("visual-item-updated"))
        self._display_items[name] = {"name": name, "label": label,
                "weight": weight, "widget": widget}
        self.core.add_item(name, widget.get_active, widget.set_active)
        self._rebuild_display_items()

    def unregister_display_item(self, name):
        if not name in self._display_items:
            self.log.debug("Failed to unregister unknown display item: %s" % \
                    name)
            return
        del self._display_items[name]
        self._rebuild_display_items()

    def _rebuild_display_items(self):
        box = self.gui.get_object("PreferencesVisibleItemsBox")
        for child in box.get_children():
            box.remove(child)
        items = self._display_items.values()
        items.sort(key=lambda item: item["weight"])
        for item in items:
            box.pack_start(item["widget"], expand=False)
        box.show_all()
    
    def register_color_setting(self, name, label, weight=100):
        if name in self._color_settings:
            self.log.debug("Tried to register color '%s' twice" % name)
            return
        # color selectors
        def get_color_wrapper(obj):
            def gtk_color_to_float():
                gtk_color = obj.get_color()
                alpha = obj.get_alpha()
                return (gtk_color.red / GTK_COLOR_MAX,
                        gtk_color.green / GTK_COLOR_MAX,
                        gtk_color.blue / GTK_COLOR_MAX,
                        alpha / GTK_COLOR_MAX)
            return gtk_color_to_float
        def set_color_wrapper(obj):
            def set_gtk_color_by_float(components):
                # use alpha if it was given
                if len(components) == 3:
                    alpha = 1.0
                else:
                    alpha = components[3]
                red, green, blue = components[:3]
                obj.set_color(gtk.gdk.Color(int(red * GTK_COLOR_MAX),
                        int(green * GTK_COLOR_MAX), int(blue * GTK_COLOR_MAX)))
                obj.set_alpha(int(alpha * GTK_COLOR_MAX))
            return set_gtk_color_by_float
        widget = gtk.ColorButton()
        widget.set_use_alpha(True)
        self._color_settings[name] = {"name": name, "label": label,
                "weight": weight, "widget": widget}
        widget.connect("color-set", lambda widget: \
                self.core.emit_event("visual-item-updated"))
        self.core.add_item(name, get_color_wrapper(widget),
                set_color_wrapper(widget))
        self._rebuild_color_settings()

    def unregister_color_setting(self, name):
        if not name in self._color_settings:
            self.log.debug("Failed to unregister unknown color item: %s" % name)
            return
        del self._color_settings[name]
        self._rebuild_color_settings()
    
    def _rebuild_color_settings(self):
        color_table = self.gui.get_object("ColorTable")
        for child in color_table.get_children():
            color_table.remove(child)
        items = self._color_settings.values()
        items.sort(key=lambda item: item["weight"])
        for index, item in enumerate(items):
            label = gtk.Label("%s:" % item["label"])
            label.set_alignment(0.0, 0.5)
            color_table.attach(label, 0, 1, index, index + 1,
                    xoptions=gtk.FILL, yoptions=gtk.FILL)
            color_table.attach(item["widget"], 1, 2, index, index + 1,
                    xoptions=gtk.FILL, yoptions=gtk.FILL)
        color_table.show_all()

    def toggle_3d_view(self, widget=None, value=None):
        current_state = self.is_visible
        if value is None:
            new_state = not current_state
        else:
            new_state = value
        if new_state == current_state:
            return
        elif new_state:
            if self.is_visible:
                self.reset_view()
            else:
                # the window is just hidden
                self.show()
        else:
            self.hide()

    def show(self):
        self.is_visible = True
        self.window.move(*self._position)
        self.window.show()

    def hide(self):
        self.is_visible = False
        self._position = self.window.get_position()
        self.window.hide()

    def key_handler(self, widget=None, event=None):
        if event is None:
            return
        try:
            keyval = getattr(event, "keyval")
            get_state = getattr(event, "get_state")
            key_string = getattr(event, "string")
        except AttributeError:
            return
        # define arrow keys and "vi"-like navigation keys
        move_keys_dict = {
                gtk.keysyms.Left: (1, 0),
                gtk.keysyms.Down: (0, -1),
                gtk.keysyms.Up: (0, 1),
                gtk.keysyms.Right: (-1, 0),
                ord("h"): (1, 0),
                ord("j"): (0, -1),
                ord("k"): (0, 1),
                ord("l"): (-1, 0),
                ord("H"): (1, 0),
                ord("J"): (0, -1),
                ord("K"): (0, 1),
                ord("L"): (-1, 0),
        }
        if key_string and (key_string in '1234567'):
            self._last_view = None
            names = ["reset", "front", "back", "left", "right", "top", "bottom"]
            index = '1234567'.index(key_string)
            self.rotate_view(view=VIEWS[names[index]])
            self._paint_ignore_busy()
        elif key_string in ('i', 'm', 's', 'p'):
            if key_string == 'i':
                key = "view_light"
            elif key_string == 'm':
                key = "view_polygon"
            elif key_string == 's':
                key = "view_shadow"
            elif key_string == 'p':
                key = "view_perspective"
            else:
                key = None
            # toggle setting
            self.core.set(key, not self.core.get(key))
            # re-init gl settings
            self.glsetup()
            self.paint()
        elif key_string in ("+", "-"):
            self._last_view = None
            if key_string == "+":
                self.camera.zoom_in()
            else:
                self.camera.zoom_out()
            self.paint()
        elif keyval in move_keys_dict.keys():
            self._last_view = None
            move_x, move_y = move_keys_dict[keyval]
            if get_state() & gtk.gdk.SHIFT_MASK:
                # shift key pressed -> rotation
                base = 0
                factor = 10
                self.camera.rotate_camera_by_screen(base, base,
                        base - factor * move_x, base - factor * move_y)
            else:
                # no shift key -> moving
                self.camera.shift_view(x_dist=move_x, y_dist=move_y)
            self.paint()
        else:
            # see dir(gtk.keysyms)
            #print "Key pressed: %s (%s)" % (keyval, get_state())
            pass

    def check_busy(func):
        def check_busy_wrapper(self, *args, **kwargs):
            if self.busy:
                return
            self.busy = True
            result = func(self, *args, **kwargs)
            self.busy = False
            return result
        return check_busy_wrapper

    def gtkgl_refresh(func):
        def gtkgl_refresh_wrapper(self, *args, **kwargs):
            prev_mode = GL.glGetIntegerv(GL.GL_MATRIX_MODE)
            GL.glMatrixMode(GL.GL_MODELVIEW)
            # clear the background with the configured color
            bg_col = self.core.get("color_background")
            GL.glClearColor(bg_col[0], bg_col[1], bg_col[2], 0.0)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT|GL.GL_DEPTH_BUFFER_BIT)
            result = func(self, *args, **kwargs)
            self.camera.position_camera()
            # adjust Light #2
            v = self.camera.view
            lightpos = (v["center"][0] + v["distance"][0],
                    v["center"][1] + v["distance"][1],
                    v["center"][2] + v["distance"][2])
            GL.glLightfv(GL.GL_LIGHT1, GL.GL_POSITION, lightpos)
            self._paint_raw()
            GL.glMatrixMode(prev_mode)
            GL.glFlush()
            self.area.get_gl_drawable().swap_buffers()
            return result
        return gtkgl_refresh_wrapper

    def glsetup(self, widget=None):
        GLUT.glutInit()
        GLUT.glutInitDisplayMode(GLUT.GLUT_RGBA | GLUT.GLUT_DOUBLE | \
                GLUT.GLUT_DEPTH | GLUT.GLUT_MULTISAMPLE | GLUT.GLUT_ALPHA | \
                GLUT.GLUT_ACCUM)
        if self.core.get("view_shadow"):
            # TODO: implement shadowing (or remove the setting)
            pass
        # use vertex normals for smooth rendering
        GL.glShadeModel(GL.GL_SMOOTH)
        bg_col = self.core.get("color_background")
        GL.glClearColor(bg_col[0], bg_col[1], bg_col[2], 0.0)
        GL.glHint(GL.GL_PERSPECTIVE_CORRECTION_HINT, GL.GL_NICEST)
        GL.glMatrixMode(GL.GL_MODELVIEW)
        # enable blending/transparency (alpha) for colors
        GL.glEnable(GL.GL_BLEND)
        # see http://wiki.delphigl.com/index.php/glBlendFunc
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
        GL.glEnable(GL.GL_DEPTH_TEST)        
        # "less" is OpenGL's default
        GL.glDepthFunc(GL.GL_LESS)   
        # slightly improved performance: ignore all faces inside the objects
        GL.glCullFace(GL.GL_BACK);
        GL.glEnable(GL.GL_CULL_FACE);
        # enable antialiasing
        GL.glEnable(GL.GL_LINE_SMOOTH)
        #GL.glEnable(GL.GL_POLYGON_SMOOTH)
        GL.glHint(GL.GL_LINE_SMOOTH_HINT, GL.GL_NICEST)
        GL.glHint(GL.GL_POLYGON_SMOOTH_HINT, GL.GL_NICEST)
        # TODO: move to toolpath drawing
        GL.glLineWidth(0.8)
        #GL.glEnable(GL.GL_MULTISAMPLE_ARB)
        GL.glEnable(GL.GL_POLYGON_OFFSET_FILL)
        GL.glPolygonOffset(1.0, 1.0)
        GL.glMaterial(GL.GL_FRONT_AND_BACK, GL.GL_AMBIENT_AND_DIFFUSE,
                self.core.get("color_model") )
        GL.glMaterial(GL.GL_FRONT_AND_BACK, GL.GL_SPECULAR,
                (1.0, 1.0, 1.0, 1.0))
        GL.glMaterial(GL.GL_FRONT_AND_BACK, GL.GL_SHININESS, (100.0))
        if self.core.get("view_polygon"):
            GL.glPolygonMode(GL.GL_FRONT_AND_BACK, GL.GL_FILL)
        else:
            GL.glPolygonMode(GL.GL_FRONT_AND_BACK, GL.GL_LINE)
        GL.glMatrixMode(GL.GL_MODELVIEW)
        GL.glLoadIdentity()
        GL.glMatrixMode(GL.GL_PROJECTION)
        GL.glLoadIdentity()
        GL.glViewport(0, 0, self.area.allocation.width,
                self.area.allocation.height)
        # lighting
        GL.glLightModeli(GL.GL_LIGHT_MODEL_LOCAL_VIEWER, GL.GL_TRUE)
        # Light #1
        # setup the ambient light
        GL.glLightfv(GL.GL_LIGHT0, GL.GL_AMBIENT, (0.3, 0.3, 0.3, 1.0))
        # setup the diffuse light
        GL.glLightfv(GL.GL_LIGHT0, GL.GL_DIFFUSE, (0.8, 0.8, 0.8, 1.0))
        # setup the specular light
        GL.glLightfv(GL.GL_LIGHT0, GL.GL_SPECULAR, (0.1, 0.1, 0.1, 1.0))
        # enable Light #1
        GL.glEnable(GL.GL_LIGHT0)
        # Light #2
        # spotlight with small light cone (like a desk lamp)
        #GL.glLightfv(GL.GL_LIGHT1, GL.GL_SPOT_CUTOFF, 10.0)
        # ... directed at the object
        v = self.camera.view
        GL.glLightfv(GL.GL_LIGHT1, GL.GL_SPOT_DIRECTION,
                (v["center"][0], v["center"][1], v["center"][2]))
        GL.glLightfv(GL.GL_LIGHT1, GL.GL_AMBIENT, (0.3, 0.3, 0.3, 1.0))
        # and dark outside of the light cone
        #GL.glLightfv(GL.GL_LIGHT1, GL.GL_SPOT_EXPONENT, 100.0)
        #GL.glLightf(GL.GL_LIGHT1, GL.GL_QUADRATIC_ATTENUATION, 0.5) 
        # setup the diffuse light
        GL.glLightfv(GL.GL_LIGHT1, GL.GL_DIFFUSE, (0.9, 0.9, 0.9, 1.0))
        # setup the specular light
        GL.glLightfv(GL.GL_LIGHT1, GL.GL_SPECULAR, (1.0, 1.0, 1.0, 1.0))
        # enable Light #2
        GL.glEnable(GL.GL_LIGHT1)       
        if self.core.get("view_light"):
            GL.glEnable(GL.GL_LIGHTING)
        else:
            GL.glDisable(GL.GL_LIGHTING)
        GL.glEnable(GL.GL_NORMALIZE)
        GL.glColorMaterial(GL.GL_FRONT_AND_BACK, GL.GL_AMBIENT_AND_DIFFUSE)
        GL.glColorMaterial(GL.GL_FRONT_AND_BACK, GL.GL_SPECULAR)
        #GL.glColorMaterial(GL.GL_FRONT_AND_BACK, GL.GL_EMISSION)
        GL.glEnable(GL.GL_COLOR_MATERIAL)

    def destroy(self, widget=None, data=None):
        self.hide()
        self.core.emit_event("visualization-state-changed")
        # don't close the window
        return True

    def gtkgl_functionwrapper(function):
        def gtkgl_functionwrapper_function(self, *args, **kwords):
            gldrawable = self.area.get_gl_drawable()
            if not gldrawable:
                return
            glcontext = self.area.get_gl_context()
            if not gldrawable.gl_begin(glcontext):
                return
            if not self.initialized:
                self.glsetup()
                self.initialized = True
            result = function(self, *args, **kwords)
            gldrawable.gl_end()
            return result
        return gtkgl_functionwrapper_function

    def _restore_latest_view(self):
        """ this function is called whenever the model list changes

        The function will restore the latest selected view - including
        automatic distance adjustment. The latest view is always reset to
        None, if any manual change (e.g. panning via mouse or keyboard)
        occoured.
        """
        if self._last_view:
            self.rotate_view(view=self._last_view)

    @check_busy
    @gtkgl_functionwrapper
    def context_menu_handler(self, widget, event):
        if (event.button == self.mouse["pressed_button"] == BUTTON_RIGHT) \
                and self.context_menu \
                and (event.get_time() - self.mouse["pressed_timestamp"] < 300) \
                and (abs(event.x - self.mouse["pressed_pos"][0]) < 3) \
                and (abs(event.y - self.mouse["pressed_pos"][1]) < 3):
            # A quick press/release cycle with the right mouse button
            # -> open the context menu.
            self.context_menu.popup(None, None, None, event.button,
                    int(event.get_time()))

    @check_busy
    @gtkgl_functionwrapper
    def scroll_handler(self, widget, event):
        """ handle events of the scroll wheel

        shift key: horizontal pan instead of vertical
        control key: zoom
        """
        remember_last_view = self._last_view
        self._last_view = None
        try:
            modifier_state = event.get_state()
        except AttributeError:
            # this should probably never happen
            return
        control_pressed = modifier_state & gtk.gdk.CONTROL_MASK
        shift_pressed = modifier_state & gtk.gdk.SHIFT_MASK
        if (event.direction == gtk.gdk.SCROLL_RIGHT) or \
                ((event.direction == gtk.gdk.SCROLL_UP) and shift_pressed):
            # horizontal move right
            self.camera.shift_view(x_dist=-1)
        elif (event.direction == gtk.gdk.SCROLL_LEFT) or \
                ((event.direction == gtk.gdk.SCROLL_DOWN) and shift_pressed):
            # horizontal move left
            self.camera.shift_view(x_dist=1)
        elif (event.direction == gtk.gdk.SCROLL_UP) and control_pressed:
            # zoom in
            self.camera.zoom_in()
        elif event.direction == gtk.gdk.SCROLL_UP:
            # vertical move up
            self.camera.shift_view(y_dist=1)
        elif (event.direction == gtk.gdk.SCROLL_DOWN) and control_pressed:
            # zoom out
            self.camera.zoom_out()
        elif event.direction == gtk.gdk.SCROLL_DOWN:
            # vertical move down
            self.camera.shift_view(y_dist=-1)
        else:
            # no interesting event -> no re-painting
            self._last_view = remember_last_view
            return
        self._paint_ignore_busy()

    def mouse_press_handler(self, widget, event):
        self.mouse["pressed_timestamp"] = event.get_time()
        self.mouse["pressed_button"] = event.button
        self.mouse["pressed_pos"] = event.x, event.y
        self.mouse_handler(widget, event)

    @check_busy
    @gtkgl_functionwrapper
    def mouse_handler(self, widget, event):
        x, y, state = event.x, event.y, event.state
        if self.mouse["button"] is None:
            if (state & BUTTON_ZOOM) or (state & BUTTON_ROTATE) \
                    or (state & BUTTON_MOVE):
                self.mouse["button"] = state
                self.mouse["start_pos"] = [x, y]
        else:
            # Don't try to create more than 25 frames per second (enough for
            # a decent visualization).
            if event.get_time() - self.mouse["event_timestamp"] < 40:
                return
            elif state & self.mouse["button"] & BUTTON_ZOOM:
                self._last_view = None
                # the start button is still active: update the view
                start_x, start_y = self.mouse["start_pos"]
                self.mouse["start_pos"] = [x, y]
                # Move the mouse from lower left to top right corner for
                # scaling up.
                scale = 1 - 0.01 * ((x - start_x) + (start_y - y))
                # do some sanity checks, scale no more than
                # 1:100 on any given click+drag
                if scale < 0.01:
                    scale = 0.01
                elif scale > 100:
                    scale = 100
                self.camera.scale_distance(scale)
                self._paint_ignore_busy()
            elif (state & self.mouse["button"] & BUTTON_MOVE) \
                    or (state & self.mouse["button"] & BUTTON_ROTATE):
                self._last_view = None
                start_x, start_y = self.mouse["start_pos"]
                self.mouse["start_pos"] = [x, y]
                if (state & BUTTON_MOVE):
                    # Determine the biggest dimension (x/y/z) for moving the
                    # screen's center in relation to this value.
                    obj_dim = []
                    low, high = pycam.Geometry.Model.get_combined_bounds(
                            self.core.get("models").get_visible())
                    for index in range(3):
                        obj_dim.append(high[index] - low[index])
                    max_dim = max(obj_dim)
                    self.camera.move_camera_by_screen(x - start_x, y - start_y,
                            max_dim)
                else:
                    # BUTTON_ROTATE
                    # update the camera position according to the mouse movement
                    self.camera.rotate_camera_by_screen(start_x, start_y, x, y)
                self._paint_ignore_busy()
            else:
                # button was released
                self.mouse["button"] = None
                self._paint_ignore_busy()
        self.mouse["event_timestamp"] = event.get_time()

    @check_busy
    @gtkgl_functionwrapper
    @gtkgl_refresh
    def rotate_view(self, widget=None, view=None):
        if view:
            self._last_view = view.copy()
        self.camera.set_view(view)

    def reset_view(self):
        self.rotate_view(view=None)

    @check_busy
    @gtkgl_functionwrapper
    @gtkgl_refresh
    def _resize_window(self, widget, data=None):
        GL.glViewport(0, 0, self.area.allocation.width,
                self.area.allocation.height)

    @check_busy
    @gtkgl_functionwrapper
    @gtkgl_refresh
    def paint(self, widget=None, data=None):
        # the decorators take core for redraw
        pass

    @gtkgl_functionwrapper
    @gtkgl_refresh
    def _paint_ignore_busy(self, widget=None):
        pass

    def _paint_raw(self, widget=None):
        # draw the model
        draw_complete_model_view(self.core)
        self.core.emit_event("visualize-items")
        # update the dimension display
        s = self.core
        dimension_bar = self.gui.get_object("view3ddimension")
        low, high = pycam.Geometry.Model.get_combined_bounds(
                self.core.get("models").get_visible())
        if not (None in low or None in high) and s.get("show_dimensions"):
            for name, size in (
                    ("model_dim_x", high[0] - low[0]),
                    ("model_dim_y", high[1] - low[1]),
                    ("model_dim_z", high[2] - low[2])):
                self.gui.get_object(name).set_text("%.3f %s" \
                        % (size, s.get("unit")))
            dimension_bar.show()
        else:
            dimension_bar.hide()


class Camera(object):

    def __init__(self, core, get_dim_func, view=None):
        self.view = None
        self.core = core
        self._get_dim_func = get_dim_func
        self.set_view(view)

    def set_view(self, view=None):
        if view is None:
            self.view = VIEWS["reset"].copy()
        else:
            self.view = view.copy()
        self.center_view()
        self.auto_adjust_distance()

    def _get_low_high_dims(self):
        # center the view on the object
        models = self.core.get("models").get_visible()
        return pycam.Geometry.Model.get_combined_bounds(models)

    def center_view(self):
        center = []
        low, high = self._get_low_high_dims()
        if None in low or None in high:
            center = [0, 0, 0]
        else:
            for index in range(3):
                center.append((low[index] + high[index]) / 2)
        self.view["center"] = center

    def auto_adjust_distance(self):
        s = self.core
        v = self.view
        # adjust the distance to get a view of the whole object
        low_high = zip(*self._get_low_high_dims())
        if (None, None) in low_high:
            return
        max_dim = max([high - low for low, high in low_high])
        distv = Point(v["distance"][0], v["distance"][1],
                v["distance"][2]).normalized()
        # The multiplier "1.25" is based on experiments. 1.414 (sqrt(2)) should
        # be roughly sufficient for showing the diagonal of any model.
        distv = distv.mul((max_dim * 1.25) / number(math.sin(v["fovy"] / 2)))
        self.view["distance"] = (distv.x, distv.y, distv.z)
        # Adjust the "far" distance for the camera to make sure, that huge
        # models (e.g. x=1000) are still visible.
        self.view["zfar"] = 100 * max_dim

    def scale_distance(self, scale):
        if scale != 0:
            scale = number(scale)
            dist = self.view["distance"]
            self.view["distance"] = (scale * dist[0], scale * dist[1],
                    scale * dist[2])

    def get(self, key, default=None):
        if (not self.view is None) and self.view.has_key(key):
            return self.view[key]
        else:
            return default

    def set(self, key, value):
        self.view[key] = value

    def move_camera_by_screen(self, x_move, y_move, max_model_shift):
        """ move the camera acoording to a mouse movement
        @type x_move: int
        @value x_move: movement of the mouse along the x axis
        @type y_move: int
        @value y_move: movement of the mouse along the y axis
        @type max_model_shift: float
        @value max_model_shift: maximum shifting of the model view (e.g. for
            x_move == screen width)
        """
        factors_x, factors_y = self._get_axes_vectors()
        width, height = self._get_screen_dimensions()
        # relation of x/y movement to the respective screen dimension
        win_x_rel = (-2 * x_move) / float(width) / math.sin(self.view["fovy"])
        win_y_rel = (-2 * y_move) / float(height) / math.sin(self.view["fovy"])
        # This code is completely arbitrarily based on trial-and-error for
        # finding a nice movement speed for all distances.
        # Anyone with a better approach should just fix this.
        distance_vector = self.get("distance")
        distance = float(sqrt(sum([dim ** 2 for dim in distance_vector])))
        win_x_rel *= math.cos(win_x_rel / distance) ** 20
        win_y_rel *= math.cos(win_y_rel / distance) ** 20
        # update the model position that should be centered on the screen
        old_center = self.view["center"]
        new_center = []
        for i in range(3):
            new_center.append(old_center[i] \
                    + max_model_shift * (number(win_x_rel) * factors_x[i] \
                    + number(win_y_rel) * factors_y[i]))
        self.view["center"] = tuple(new_center)

    def rotate_camera_by_screen(self, start_x, start_y, end_x, end_y):
        factors_x, factors_y = self._get_axes_vectors()
        width, height = self._get_screen_dimensions()
        # calculate rotation factors - based on the distance to the center
        # (between -1 and 1)
        rot_x_factor = (2.0 * start_x) / width - 1
        rot_y_factor = (2.0 * start_y) / height - 1
        # calculate rotation angles (between -90 and +90 degrees)
        xdiff = end_x - start_x
        ydiff = end_y - start_y
        # compensate inverse rotation left/right side (around x axis) and
        # top/bottom (around y axis)
        if rot_x_factor < 0:
            ydiff = -ydiff
        if rot_y_factor > 0:
            xdiff = -xdiff
        rot_x_angle = rot_x_factor * math.pi * ydiff / height
        rot_y_angle = rot_y_factor * math.pi * xdiff / width
        # rotate around the "up" vector with the y-axis rotation
        original_distance = self.view["distance"]
        original_up = self.view["up"]
        y_rot_matrix = Matrix.get_rotation_matrix_axis_angle(factors_y,
                rot_y_angle)
        new_distance = Matrix.multiply_vector_matrix(original_distance,
                y_rot_matrix)
        new_up = Matrix.multiply_vector_matrix(original_up, y_rot_matrix)
        # rotate around the cross vector with the x-axis rotation
        x_rot_matrix = Matrix.get_rotation_matrix_axis_angle(factors_x,
                rot_x_angle)
        new_distance = Matrix.multiply_vector_matrix(new_distance, x_rot_matrix)
        new_up = Matrix.multiply_vector_matrix(new_up, x_rot_matrix)
        self.view["distance"] = new_distance
        self.view["up"] = new_up

    def position_camera(self):
        width, height = self._get_screen_dimensions()
        prev_mode = GL.glGetIntegerv(GL.GL_MATRIX_MODE)
        GL.glMatrixMode(GL.GL_PROJECTION)
        GL.glLoadIdentity()
        v = self.view
        # position the light according to the current bounding box
        light_pos = [0, 0, 0]
        low, high = self._get_low_high_dims()
        if not None in low and not None in high:
            for index in range(3):
                light_pos[index] = 2 * (high[index] - low[index])
        GL.glLightfv(GL.GL_LIGHT0, GL.GL_POSITION, (light_pos[0], light_pos[1],
                light_pos[2], 0.0))
        # position the camera
        camera_position = (v["center"][0] + v["distance"][0],
                v["center"][1] + v["distance"][1],
                v["center"][2] + v["distance"][2])
        # position a second light at camera position
        GL.glLightfv(GL.GL_LIGHT1, GL.GL_POSITION, (camera_position[0],
                camera_position[1], camera_position[2], 0.0))
        if self.core.get("view_perspective"):
            # perspective view
            GLU.gluPerspective(v["fovy"], (0.0 + width) / height, v["znear"],
                    v["zfar"])
        else:
            # parallel projection
            # This distance calculation is completely based on trial-and-error.
            distance = math.sqrt(sum([d ** 2 for d in v["distance"]]))
            distance *= math.log(math.sqrt(width * height)) / math.log(10)
            sin_factor = math.sin(v["fovy"] / 360.0 * math.pi) * distance
            left = v["center"][0] - sin_factor
            right = v["center"][0] + sin_factor
            top = v["center"][1] + sin_factor
            bottom = v["center"][1] - sin_factor
            near = v["center"][2] - 2 * sin_factor
            far = v["center"][2] + 2 * sin_factor
            GL.glOrtho(left, right, bottom, top, near, far)
        GLU.gluLookAt(camera_position[0], camera_position[1],
                camera_position[2], v["center"][0], v["center"][1],
                v["center"][2], v["up"][0], v["up"][1], v["up"][2])
        GL.glMatrixMode(prev_mode)

    def shift_view(self, x_dist=0, y_dist=0):
        obj_dim = []
        low, high = self._get_low_high_dims()
        for index in range(3):
            obj_dim.append(high[index] - low[index])
        max_dim = max(obj_dim)
        factor = 50
        self.move_camera_by_screen(x_dist * factor, y_dist * factor, max_dim)

    def zoom_in(self):
        self.scale_distance(sqrt(0.5))

    def zoom_out(self):
        self.scale_distance(sqrt(2))

    def _get_screen_dimensions(self):
        return self._get_dim_func()

    def _get_axes_vectors(self):
        """calculate the model vectors along the screen's x and y axes"""
        # The "up" vector defines, in what proportion each axis of the model is
        # in line with the screen's y axis.
        v_up = self.view["up"]
        factors_y = (number(v_up[0]), number(v_up[1]), number(v_up[2]))
        # Calculate the proportion of each model axis according to the x axis of
        # the screen.
        distv = self.view["distance"]
        distv = Point(distv[0], distv[1], distv[2]).normalized()
        factors_x = distv.cross(Point(v_up[0], v_up[1], v_up[2])).normalized()
        factors_x = (factors_x.x, factors_x.y, factors_x.z)
        return (factors_x, factors_y)


