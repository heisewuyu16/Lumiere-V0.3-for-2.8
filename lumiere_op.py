import bpy
from .lumiere_utils import (
	raycast_light,
	export_props_light,
	get_lumiere_dict,
	)
from .lumiere_draw import (
	draw_callback_2d,
	draw_callback_3d,
	)

from .lumiere_lights import (
	create_softbox,
	)

import enum

from bpy.types import Operator

from mathutils import (
	Vector,
	Matrix,
	Quaternion,
	Euler,
	)

from math import (
	sin,
	cos,
	pi,
	)

import bgl
import blf
import bmesh
import gpu
import os
from gpu_extras.batch import batch_for_shader
import json

# -------------------------------------------------------------------- #
class LUMIERE_OT_export_light(Operator):
	"""Export the current light data in JSON format"""

	bl_idname = "object.export_light"
	bl_label = "Export light"


	def execute(self, context):
		current_file_path = __file__
		current_file_dir = os.path.dirname(__file__)
		light = context.active_object

	#---Try to open the Lumiere export dictionary
		try:
			with open(current_file_dir + "\\" + "lumiere_dictionary.json", 'r', encoding='utf-8') as file:
				my_dict = json.load(file)
				file.close()
		except Exception:
			print("Warning, dict empty, creating a new one.")
			my_dict = {}

		lumiere_dict = export_props_light(self, context)

		my_dict.update(lumiere_dict)

		with open(current_file_dir + "\\" + "lumiere_dictionary.json", "w", encoding='utf-8') as file:
			json.dump(my_dict, file, sort_keys=True, indent=4, ensure_ascii=False)

		file.close()
		message = "Light exported"
		self.report({'INFO'}, message)
		return {'FINISHED'}


# -------------------------------------------------------------------- #
# class LUMIERE_OT_RemoveLightItem(bpy.types.Operator):
# 	bl_idname = "scene.remove_light_item"
# 	bl_label = "Remove Light Entry"
#
# 	light = bpy.props.StringProperty()
#
# 	@classmethod
# 	def poll(cls, context):
# 		return context.scene.Lumiere_all_lights_list_index >= 0
#
# 	def execute(self, context):
# 		settings = context.scene.Lumiere_all_lights_list
# 		settings.remove(context.scene.Lumiere_all_lights_list_index)
# 		context.scene.Lumiere_all_lights_list_index -= 1
# 		self.my_dict = get_lumiere_dict(self, context)
# 		self.report({'INFO'}, "Light " + self.light + " deleted from the list")
# 		self.my_dict.pop(self.light, None)
# 		update_lumiere_dict(self, context, self.my_dict)
#
# 		return {'FINISHED'}
# -------------------------------------------------------------------- #

class OpStatus(object):
	"""Operator status : Running or not"""
	running = False

	def __init__(cls, value):
		cls.value = running

class LUMIERE_OT_ray_operator(Operator):
	bl_idname = "lumiere.ray_operator"
	bl_label = "Lighting operator"
	bl_description = "Click to enter in interactive lighting mode"
	bl_options = {'REGISTER', 'UNDO'}


	@classmethod
	def poll(cls, context):
		return context.area.type == 'VIEW_3D' and context.mode == 'OBJECT'

	def __init__(self):
		self.draw_handle_2d = None
		self.draw_handle_3d = None
		self.light_selected = False
		self.shift = False
		self.ctrl = False
		self.lmb = False
		self.is_running = False
		self.create_collection()

	def invoke(self, context, event):
		print("LUMIERE RUNNING ...")
		args = (self, context)
		self.lumiere_context = context
		if context.space_data.type == 'VIEW_3D':
			self.lumiere_area = context.area
		self.enable_cursor = context.space_data.overlay.show_cursor
		self.enable_navigate = context.space_data.show_gizmo_navigate
		self.enable_tool = context.space_data.show_gizmo_tool
		self.relat_lines = context.space_data.overlay.show_relationship_lines

		self.register_handlers(args, context)
		context.window_manager.modal_handler_add(self)
		return {"RUNNING_MODAL"}

	def register_handlers(self, args, context):
		if self.is_running == False:
			OpStatus.running = True
			self.is_running = True
			self.draw_handle_2d = bpy.types.SpaceView3D.draw_handler_add(draw_callback_2d, args, "WINDOW", "POST_PIXEL")
			self.draw_handle_3d = bpy.types.SpaceView3D.draw_handler_add(draw_callback_3d, args, "WINDOW", "POST_VIEW")

	def unregister_handlers(self, context):
		bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle_2d, "WINDOW")
		bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle_3d, "WINDOW")
		self.draw_handle_2d = None
		self.draw_handle_3d = None
		OpStatus.running = False

		if context.view_layer.active_layer_collection.name == "Lumiere":
			context.view_layer.active_layer_collection = context.view_layer.layer_collection

	def modal(self, context, event):
		# Find the limit of the view3d region
		check_region(self,context,event)

		# Is the object selected is from Lumiere collection
		check_light_selected(self, context)

		# Hide 3d cursor
		if self.in_view_3d:
			context.space_data.overlay.show_cursor = False
			context.space_data.show_gizmo_navigate = False
			context.space_data.show_gizmo_tool = False
			context.space_data.overlay.show_relationship_lines = False

		try:
			# Shift press
			self.shift = True if event.shift else False

			# Ctrl press
			self.ctrl = True if event.ctrl else False

			if context.area != self.lumiere_area:
				self.is_running = False
				self.unregister_handlers(context)
				print("LUMIERE CANCELLED ...")
				return {'CANCELLED'}

			if event.type in {"ESC", "RIGHTMOUSE"} :
				print("LUMIERE EXIT ...")
				self.unregister_handlers(context)

				# State of 3d cursor before Lumiere
				context.space_data.overlay.show_cursor = self.enable_cursor
				context.space_data.show_gizmo_navigate = self.enable_navigate
				context.space_data.show_gizmo_tool = self.enable_tool
				context.space_data.overlay.show_relationship_lines = self.relat_lines
				self.is_running = False

				return {'CANCELLED'}

			# Left mouse button press
			elif event.type == 'LEFTMOUSE' and self.in_view_3d:
				self.lmb = event.value == 'PRESS'

			# Allow navigation
			elif event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE'} or event.type.startswith("NUMPAD"):
				return {'PASS_THROUGH'}

			# Left mouse button pressed with an object from Lumiere collection
			if self.lmb and self.in_view_3d:
				if self.light_selected :
					# Raycast to move the light compared to the targeted object
					raycast_light(self, event, context, context.object.Lumiere.range)
				else:
					create_softbox()

				return {'RUNNING_MODAL'}

			return {"PASS_THROUGH"}

		except:
			print("\n[Lumiere ERROR]\n")
			import traceback
			traceback.print_exc()
			self.unregister_handlers(context)

			self.report({'WARNING'},
						"Operation finished. (Check the console for more info)")

			return {'FINISHED'}

	def finish(self):
		return {"FINISHED"}

	def create_collection(self):
		# Create a new collection and link it to the scene.
		if 'Lumiere' not in bpy.context.scene.collection.children.keys() :
			_lumiere_coll = bpy.data.collections.new("Lumiere")
			bpy.context.scene.collection.children.link(_lumiere_coll)



# Utilities
###############################################
# Get the region area where the operator is used
def check_region(self,context,event):
	if context.area != None:
		if context.area.type == "VIEW_3D" :
			for region in context.area.regions:
				if region.type == "TOOLS":
					t_panel = region
				elif region.type == "UI":
					ui_panel = region

			view_3d_region_x = Vector((context.area.x + t_panel.width, context.area.x + context.area.width - (ui_panel.width+1)))
			view_3d_region_y = Vector((context.region.y, context.region.y + context.region.height))

			if (event.mouse_x > view_3d_region_x[0] and event.mouse_x < view_3d_region_x[1] \
			and event.mouse_y > view_3d_region_y[0] and event.mouse_y < view_3d_region_y[1]):
				self.in_view_3d = True
				return True
			else:
				self.in_view_3d = False
				return False

		else:
			self.in_view_3d = False
			return False

# Check if the object selected is a light from Lumiere
def check_light_selected(self, context):
	if (context.view_layer.objects.active is not None):
			if context.view_layer.objects.active.name in context.scene.collection.children['Lumiere'].all_objects:
				self.light_selected = True
			else:
				self.light_selected = False


# -------------------------------------------------------------------- #
## Register

classes = [
	LUMIERE_OT_export_light,
	LUMIERE_OT_ray_operator,
	]

def register():
	from bpy.utils import register_class
	for cls in classes:
		print("CLASSE: ", cls)
		register_class(cls)

def unregister():
	from bpy.utils import unregister_class
	for cls in reversed(classes):
		unregister_class(cls)
