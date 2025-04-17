import bpy
import math
import mathutils
import json
import threading
import socket
import time
import requests
import tempfile
import traceback
import os
import shutil
import random
import base64
from PIL import Image
from mathutils import Vector, Color 
from datetime import datetime
from math import cos, sin, pi
from bpy.props import StringProperty, IntProperty, BoolProperty, EnumProperty

bl_info = {
    "name": "Blender MCP",
    "author": "BlenderMCP",
    "version": (0, 2),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > BlenderMCP",
    "description": "Connect Blender to Claude via MCP",
    "category": "Interface",
}

RODIN_FREE_TRIAL_KEY = "k9TcfFoEhNd9cCPP2guHAHHHkctZHIRhZDywZ1euGUXwihbYLpOjQhofby80NJez"

class BlenderMCPServer:
    def __init__(self, host='localhost', port=9876):
        self.host = host
        self.port = port
        self.running = False
        self.socket = None
        self.server_thread = None
        self.used_colors = []
        self.min_hue_diff = 0.3
    
    def nearby_objects(self, name, distance_multiplier=1.0):
        # Identifying objects in proximity to the specified object
        target_obj = bpy.data.objects.get(name)
        if not target_obj:
            return []
        
        bbox_corners = [target_obj.matrix_world @ mathutils.Vector(corner) 
                for corner in target_obj.bound_box]
    
        min_corner = bbox_corners[0].copy()
        max_corner = bbox_corners[0].copy()
        
        for corner in bbox_corners[1:]:
            min_corner.x = min(min_corner.x, corner.x)
            min_corner.y = min(min_corner.y, corner.y)
            min_corner.z = min(min_corner.z, corner.z)
            
            max_corner.x = max(max_corner.x, corner.x)
            max_corner.y = max(max_corner.y, corner.y)
            max_corner.z = max(max_corner.z, corner.z)
        
        bbox_size = max_corner - min_corner
        expanded_min = min_corner - bbox_size * distance_multiplier
        expanded_max = max_corner + bbox_size * distance_multiplier
        target_bbox = (expanded_min, expanded_max)
        
        intersecting_objects = []
        for obj in bpy.context.scene.objects:
            if obj == target_obj or obj.type != 'MESH':
                continue
                
            obj_bbox_corners = [obj.matrix_world @ mathutils.Vector(corner) 
                            for corner in obj.bound_box]
            
            obj_min = obj_bbox_corners[0].copy()
            obj_max = obj_bbox_corners[0].copy()
            
            for corner in obj_bbox_corners[1:]:
                obj_min.x = min(obj_min.x, corner.x)
                obj_min.y = min(obj_min.y, corner.y)
                obj_min.z = min(obj_min.z, corner.z)
                
                obj_max.x = max(obj_max.x, corner.x)
                obj_max.y = max(obj_max.y, corner.y)
                obj_max.z = max(obj_max.z, corner.z)
            
            if (obj_max.x > target_bbox[0].x and obj_min.x < target_bbox[1].x and
                obj_max.y > target_bbox[0].y and obj_min.y < target_bbox[1].y and
                obj_max.z > target_bbox[0].z and obj_min.z < target_bbox[1].z):
                intersecting_objects.append(obj.name)
        
        return intersecting_objects
              
    def random_color(self):
        """Generate a random, distinct color."""
        attempts = 0
        max_attempts = 50
        
        while attempts < max_attempts:
            hue = random.random()
            saturation = random.uniform(0.6, 0.9)
            value = random.uniform(0.6, 0.9)
            
            color = Color()
            color.hsv = (hue, saturation, value)
            new_color = (color.r, color.g, color.b, 1.0)
            
            if not self.used_colors or self.is_color_different_enough(new_color):
                self.used_colors.append(new_color)
                return new_color
            
            attempts += 1
            
        if len(self.used_colors) > 100:
            self.used_colors.pop(0)
        return (random.random(), random.uniform(0.3,0.85), random.uniform(0.3,0.85), 1.0)
    
    def is_color_different_enough(self, new_color):
        """check difference between new color and used colors"""
        new_color_hsv = Color((new_color[0], new_color[1], new_color[2])).hsv
        
        for used_color in self.used_colors:
            used_color_hsv = Color((used_color[0], used_color[1], used_color[2])).hsv
            hue_diff = min(
                abs(new_color_hsv[0] - used_color_hsv[0]),
                1 - abs(new_color_hsv[0] - used_color_hsv[0])
            )
            if hue_diff < self.min_hue_diff:
                return False
        return True
    
    def random_color_by_name(self, name):
        try:
            """Force a new color for the object ( regardless of existing color )"""
            obj = bpy.data.objects.get(name)
            if not obj:
                return None
            
            nearby_colors = []
            for nearby_name in self.nearby_objects(name, distance_multiplier=1.5):
                nearby_obj = bpy.data.objects.get(nearby_name)
                if nearby_obj and hasattr(nearby_obj, 'color'):
                    nearby_colors.append(nearby_obj.color[:3])
            
            for _ in range(50):
                color = self.random_color()
                if all(self.color_distance(color[:3], nc) >= 0.3 for nc in nearby_colors):
                    return color
            
            return self.random_color()
        except Exception as e:
            print(f"Error generating color for {name}: {str(e)}")
            return (0.8, 0.8, 0.8, 1.0)
        
    def color_distance(self, color1, color2):
        """Calculate the difference between two colors."""
        # HSV more suitable for color distance
        hsv1 = Color(color1).hsv
        hsv2 = Color(color2).hsv

        hue_diff = min(abs(hsv1[0] - hsv2[0]), 1 - abs(hsv1[0] - hsv2[0]))
        sat_diff = abs(hsv1[1] - hsv2[1])
        val_diff = abs(hsv1[2] - hsv2[2])

        return 0.6 * hue_diff + 0.2 * sat_diff + 0.2 * val_diff

    def calculate_farthest_point(self,center, objects):
        """Calculate the farthest point from center using bounding boxes"""
        max_distance = 0
        
        for obj in objects:
            if not hasattr(obj, 'bound_box'):
                continue
                
            bbox_world = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
            
            for point in bbox_world:
                distance = (Vector(point) - Vector(center)).length
                if distance > max_distance:
                    max_distance = distance            
        return max_distance    
    
    def calculate_distance_from_object(self,obj):
        """Calculate the recommended distance (half of the longest side) based on the object's bounding box."""
        bbox_corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
        
        min_x = min(c.x for c in bbox_corners)
        max_x = max(c.x for c in bbox_corners)
        min_y = min(c.y for c in bbox_corners)
        max_y = max(c.y for c in bbox_corners)
        min_z = min(c.z for c in bbox_corners)
        max_z = max(c.z for c in bbox_corners)
        
        size_x = max_x - min_x
        size_y = max_y - min_y
        size_z = max_z - min_z
        
        return max(size_x, size_y, size_z)*1.6
    
    def get_ortho_scale(self,distance, margin_factor=1.2):
        """Calculate ortho_scale dynamically based on the distance (furthest distance)."""
        return distance * margin_factor
    
    def calculate_perspective_camera_position(self, target_position, distance, angle=45):
        """Calculate optimal perspective camera position to center the object"""
        angle_rad = math.radians(angle)
        
        offset = distance * math.cos(angle_rad)
        height = distance * math.sin(angle_rad)
        
        return (
            target_position[0] + offset,
            target_position[1] - offset,
            target_position[2] + height
        )
        
    def create_movable_camera(self,distance,name):
        "'create single movable camera'"
        if "Viewpoint_Camera_Movable" in bpy.data.objects:
            cam = bpy.data.objects["Viewpoint_Camera_Movable"]
            print("Found existing camera, reusing it")
        else:
            bpy.ops.object.camera_add()
            cam = bpy.context.active_object
            cam.name = "Viewpoint_Camera_Movable"
            cam.data.type = 'ORTHO'
            print("Created new camera")

        bpy.context.view_layer.objects.active = cam
        cam.select_set(True)
        
        cam.data.ortho_scale = self.get_ortho_scale(distance)
        
        if name and name.strip():
            cam.data.clip_start = distance * 0.4
        else:
            cam.data.clip_start = distance * 0.666
        
        return cam
    
    def render_images(self, position, name,scene_distance):
        if name and name.strip():
            try:
                #find and highlight
                obj = bpy.data.objects[name]
                bpy.context.view_layer.objects.active = obj
                obj.select_set(True)
                  
                obj.color = self.random_color()
                target_position = obj.location
                distance=self.calculate_distance_from_object(obj)
                print(f"Found object '{name}', position: {target_position}, auto distance: {distance:.2f}")
            except KeyError:
                print(f"Error: Object '{name}' not found in scene! Using default position.")
                target_position = position
                distance = 5  #not found obj 
        else:
            bpy.ops.object.select_all(action='DESELECT')
            target_position = position
            distance = scene_distance  #get scne distance
            print(f"position: {target_position}, auto distance: {distance:.2f}")
        
        for area in bpy.context.screen.areas:
            if area.type == 'VIEW_3D':
                space = area.spaces.active
                space.shading.type = 'SOLID'
                space.shading.color_type = 'OBJECT'
                space.shading.show_xray = False
                space.shading.background_type = 'THEME'
                space.shading.background_color = (0.1, 0.1, 0.1)  # gray background
                space.overlay.show_floor = True
                space.overlay.show_axis_x = True
                space.overlay.show_axis_y = True
                space.overlay.show_axis_z = True
                
        temp_dir = bpy.app.tempdir
        output_dir = os.path.join(temp_dir, "viewpoint_screenshots")
        os.makedirs(output_dir, exist_ok=True)
        
        views = [
            {
                "name": "Front",
                "location": (target_position[0], target_position[1] - distance, target_position[2]),
                "rotation": (1.5708, 0, 0),
                "ortho_scale": self.get_ortho_scale(distance),
                "camera_type": "ORTHO"
            },
            {
                "name": "Front_Angle",
                "location": self.calculate_perspective_camera_position(target_position, distance),
                "rotation": (math.radians(30), 0, math.radians(45)),  # 30度俯角，45度水平旋转
                "fov": 45.0,
                "camera_type": "PERSP",
                "look_at": target_position
            },
            {
                "name": "Back",
                "location": (target_position[0], target_position[1] + distance, target_position[2]),
                "rotation": (1.5708, 0, 3.14159),
                "ortho_scale": self.get_ortho_scale(distance),
                "camera_type": "ORTHO"
            },
            {
                "name": "Top",
                "location": (target_position[0], target_position[1], target_position[2] + distance),
                "rotation": (0, 0, 0),
                "ortho_scale": self.get_ortho_scale(distance),
                "camera_type": "ORTHO"
            }
        ]
        
        camera = self.create_movable_camera(distance,name)
        temp_images = []
        composite_path=None
        try:
            for i, view in enumerate(views):
                camera.location = view["location"]
                camera.rotation_euler = view["rotation"]
                
                if view.get("camera_type") == "PERSP" and "look_at" in view:
                    direction = Vector(view["look_at"]) - Vector(camera.location)
                    rot_quat = direction.to_track_quat('-Z', 'Y')
                    camera.rotation_euler = rot_quat.to_euler()
                    
                camera.data.type = view.get("camera_type", "ORTHO")
                if camera.data.type == 'PERSP':
                    camera.data.lens = 35  
                    if "fov" in view:
                        camera.data.angle = math.radians(view["fov"])
                else:
                    camera.data.ortho_scale = view["ortho_scale"]
                
                bpy.context.scene.camera = camera
                
                for area in bpy.context.window.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.spaces.active.region_3d.view_perspective = 'CAMERA'
                        break
                
                bpy.context.scene.render.resolution_x = 384
                bpy.context.scene.render.resolution_y = 384
                bpy.context.scene.render.resolution_percentage = 100
                bpy.context.scene.render.image_settings.file_format = 'PNG'
                bpy.context.scene.render.image_settings.color_mode = 'RGB'
                bpy.context.scene.render.film_transparent = False
                
                temp_path = os.path.join(temp_dir, f"temp_view_{i}.png")
                bpy.context.scene.render.filepath = temp_path
                
                try:
                    bpy.ops.render.opengl(write_still=True)
                    if os.path.exists(temp_path):
                        temp_images.append(temp_path)
                        print(f"Rendered view {i+1}: {temp_path}")
                    else:
                        print(f"Render failed for view {i+1}")
                except Exception as e:
                    print(f"Render failed for view {i+1}: {str(e)}")

            
            if len(temp_images) == 4:
                try:
                    images = [Image.open(img_path) for img_path in temp_images]
                    
                    gap_size = 2 
                    border_size = 3   
                    
                    width, height = images[0].size
                    composite_width = width * 2 + gap_size + border_size * 2
                    composite_height = height * 2 + gap_size + border_size * 2
                    
                    composite = Image.new('RGB', (composite_width, composite_height), color=(255, 255, 255))
                    
                    composite.paste(images[0], (border_size, border_size))
                    composite.paste(images[1], (width + gap_size + border_size, border_size))
                    composite.paste(images[2], (border_size, height + gap_size + border_size))
                    composite.paste(images[3], (width + gap_size + border_size, height + gap_size + border_size))
                    
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    rand = random.randint(100, 999)
                    composite_path = os.path.join(output_dir, f"composite_view_{timestamp}_{rand}.png")
                    composite.save(composite_path)
                    print(f"Created composite image: {composite_path}")
                    
                    for img in images:
                        img.close()
                except Exception as e:
                    print(f"Error creating composite image: {str(e)}")
            else:
                print(f"Cannot create composite, only {len(temp_images)} images available (need 4).")

        finally:
            for area in bpy.context.window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.spaces.active.region_3d.view_perspective = 'PERSP'
                    break
            for temp_img in temp_images:
                try:
                    os.remove(temp_img)
                except:
                    pass

        return composite_path
    
    def start(self):
        if self.running:
            print("Server is already running")
            return
            
        self.running = True
        
        try:
            # Create socket
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            self.socket.listen(1)
            
            # Start server thread
            self.server_thread = threading.Thread(target=self._server_loop)
            self.server_thread.daemon = True
            self.server_thread.start()
            
            print(f"BlenderMCP server started on {self.host}:{self.port}")
        except Exception as e:
            print(f"Failed to start server: {str(e)}")
            self.stop()
            
    def stop(self):
        self.running = False
        
        # Close socket
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
            self.socket = None
        
        # Wait for thread to finish
        if self.server_thread:
            try:
                if self.server_thread.is_alive():
                    self.server_thread.join(timeout=1.0)
            except:
                pass
            self.server_thread = None
        
        print("BlenderMCP server stopped")
    
    def _server_loop(self):
        """Main server loop in a separate thread"""
        print("Server thread started")
        self.socket.settimeout(1.0)  # Timeout to allow for stopping
        
        while self.running:
            try:
                # Accept new connection
                try:
                    client, address = self.socket.accept()
                    print(f"Connected to client: {address}")
                    
                    # Handle client in a separate thread
                    client_thread = threading.Thread(
                        target=self._handle_client,
                        args=(client,)
                    )
                    client_thread.daemon = True
                    client_thread.start()
                except socket.timeout:
                    # Just check running condition
                    continue
                except Exception as e:
                    print(f"Error accepting connection: {str(e)}")
                    time.sleep(0.5)
            except Exception as e:
                print(f"Error in server loop: {str(e)}")
                if not self.running:
                    break
                time.sleep(0.5)
        
        print("Server thread stopped")
    
    def _handle_client(self, client):
        """Handle connected client"""
        print("Client handler started")
        client.settimeout(None)  # No timeout
        buffer = b''
        
        try:
            while self.running:
                # Receive data
                try:
                    data = client.recv(8192)
                    if not data:
                        print("Client disconnected")
                        break
                    
                    buffer += data
                    try:
                        # Try to parse command
                        command = json.loads(buffer.decode('utf-8'))
                        buffer = b''
                        
                        # Execute command in Blender's main thread
                        def execute_wrapper():
                            try:
                                response = self.execute_command(command)
                                response_json = json.dumps(response)
                                try:
                                    client.sendall(response_json.encode('utf-8'))
                                except:
                                    print("Failed to send response - client disconnected")
                            except Exception as e:
                                print(f"Error executing command: {str(e)}")
                                traceback.print_exc()
                                try:
                                    error_response = {
                                        "status": "error",
                                        "message": str(e)
                                    }
                                    client.sendall(json.dumps(error_response).encode('utf-8'))
                                except:
                                    pass
                            return None
                        
                        # Schedule execution in main thread
                        bpy.app.timers.register(execute_wrapper, first_interval=0.0)
                    except json.JSONDecodeError:
                        # Incomplete data, wait for more
                        pass
                except Exception as e:
                    print(f"Error receiving data: {str(e)}")
                    break
        except Exception as e:
            print(f"Error in client handler: {str(e)}")
        finally:
            try:
                client.close()
            except:
                pass
            print("Client handler stopped")

    def execute_command(self, command):
        """Execute a command in the main Blender thread"""
        try:
            cmd_type = command.get("type")
            params = command.get("params", {})
            
            # Ensure we're in the right context
            if cmd_type in ["create_object", "modify_object","duplicate_object", "delete_object"]:
                override = bpy.context.copy()
                override['area'] = [area for area in bpy.context.screen.areas if area.type == 'VIEW_3D'][0]
                with bpy.context.temp_override(**override):
                    return self._execute_command_internal(command)
            else:
                return self._execute_command_internal(command)
                
        except Exception as e:
            print(f"Error executing command: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def _execute_command_internal(self, command):
        """Internal command execution with proper context"""
        cmd_type = command.get("type")
        params = command.get("params", {})

        # Add a handler for checking PolyHaven status
        if cmd_type == "get_polyhaven_status":
            return {"status": "success", "result": self.get_polyhaven_status()}
        
        # Base handlers that are always available
        handlers = {
            "get_scene_info": self.get_scene_info,
            "create_object": self.create_object,
            "modify_object": self.modify_object,
            "delete_object": self.delete_object,
            "get_object_info": self.get_object_info,
            "execute_code": self.execute_code,
            "set_material": self.set_material,
            "get_polyhaven_status": self.get_polyhaven_status,
            "get_hyper3d_status": self.get_hyper3d_status,
            "duplicate_object": self.duplicate_object,
        }
        
        # Add Polyhaven handlers only if enabled
        if bpy.context.scene.blendermcp_use_polyhaven:
            polyhaven_handlers = {
                "get_polyhaven_categories": self.get_polyhaven_categories,
                "search_polyhaven_assets": self.search_polyhaven_assets,
                "download_polyhaven_asset": self.download_polyhaven_asset,
                "set_texture": self.set_texture,
            }
            handlers.update(polyhaven_handlers)
        
        # Add Hyper3d handlers only if enabled
        if bpy.context.scene.blendermcp_use_hyper3d:
            polyhaven_handlers = {
                "create_rodin_job": self.create_rodin_job,
                "poll_rodin_job_status": self.poll_rodin_job_status,
                "import_generated_asset": self.import_generated_asset,
            }
            handlers.update(polyhaven_handlers)

        handler = handlers.get(cmd_type)
        if handler:
            try:
                print(f"Executing handler for {cmd_type}")
                result = handler(**params)
                print(f"Handler execution complete")
                return {"status": "success", "result": result}
            except Exception as e:
                print(f"Error in handler: {str(e)}")
                traceback.print_exc()
                return {"status": "error", "message": str(e)}
        else:
            return {"status": "error", "message": f"Unknown command type: {cmd_type}"}

    
    def get_simple_info(self):
        """Get basic Blender information"""
        return {
            "blender_version": ".".join(str(v) for v in bpy.app.version),
            "scene_name": bpy.context.scene.name,
            "object_count": len(bpy.context.scene.objects)
        }
        
    def get_scene_info(self):
        """Get information about the current Blender scene"""
        try:
            print("Getting scene info...")
            # Simplify the scene info to reduce data size
            scene_info = {
                "name": bpy.context.scene.name,
                "object_count": len(bpy.context.scene.objects),
                "objects": [],
                "materials_count": len(bpy.data.materials),
            }
            
            total_x = 0.0
            total_y = 0.0
            total_z = 0.0
            valid_objects = 0
            
            objects_to_process = []
            
            for i, obj in enumerate(bpy.context.scene.objects):
                new_color = self.random_color_by_name(obj.name)
                obj.color = new_color
                
                # Collect minimal object information (limit to first 10 objects)
                if i < 10:
                    
                    obj_info = {
                        "name": obj.name,
                        "type": obj.type,
                        "location": [round(float(obj.location.x), 2), 
                                    round(float(obj.location.y), 2), 
                                    round(float(obj.location.z), 2)],
                        "color":new_color,
                    }
                    scene_info["objects"].append(obj_info)
                    
                    objects_to_process.append(obj)
                    if hasattr(obj, 'location'):
                        total_x += obj.location.x
                        total_y += obj.location.y
                        total_z += obj.location.z
                        valid_objects += 1
            
            if valid_objects > 0:
                center_x = total_x / valid_objects
                center_y = total_y / valid_objects
                center_z = total_z / valid_objects
                scene_center = Vector((center_x, center_y, center_z))
                scene_info["scene_center"] = [
                    round(center_x, 2),
                    round(center_y, 2),
                    round(center_z, 2)
                ]
            else:
                scene_center = Vector((0, 0, 0))
                scene_info["scene_center"] = [0, 0, 0]
            max_distance = self.calculate_farthest_point(scene_center, objects_to_process) #camera distance
            
            scene_info["images"] = self.render_images(scene_center,"", max_distance)

            print(f"Scene info collected: {len(scene_info['objects'])} objects")
            return scene_info
        except Exception as e:
            print(f"Error in get_scene_info: {str(e)}")
            traceback.print_exc()
            return {"error": str(e)}
    
    @staticmethod
    def _get_aabb(obj):
        """ Returns the world-space axis-aligned bounding box (AABB) of an object. """
        if obj.type != 'MESH':
            raise TypeError("Object must be a mesh")

        # Get the bounding box corners in local space
        local_bbox_corners = [mathutils.Vector(corner) for corner in obj.bound_box]

        # Convert to world coordinates
        world_bbox_corners = [obj.matrix_world @ corner for corner in local_bbox_corners]

        # Compute axis-aligned min/max coordinates
        min_corner = mathutils.Vector(map(min, zip(*world_bbox_corners)))
        max_corner = mathutils.Vector(map(max, zip(*world_bbox_corners)))

        return [
            [*min_corner], [*max_corner]
        ]

    def create_object(self, type="CUBE", name=None, location=(0, 0, 0),rotation=(0, 0, 0), scale=(1, 1, 1),
                    align="WORLD", major_segments=48, minor_segments=12, mode="MAJOR_MINOR",
                    major_radius=1.0, minor_radius=0.25, abso_major_rad=1.25, abso_minor_rad=0.75, generate_uvs=True,
                    custom_properties=None):
        """Create a new object in the scene"""
        try:
            # Deselect all objects first
            bpy.ops.object.select_all(action='DESELECT')
            
            # Create the object based on type
            if type == "CUBE":
                bpy.ops.mesh.primitive_cube_add(location=location, rotation=rotation, scale=scale)
            elif type == "SPHERE":
                bpy.ops.mesh.primitive_uv_sphere_add(location=location, rotation=rotation, scale=scale)
            elif type == "CYLINDER":
                bpy.ops.mesh.primitive_cylinder_add(location=location, rotation=rotation, scale=scale)
            elif type == "PLANE":
                bpy.ops.mesh.primitive_plane_add(location=location, rotation=rotation, scale=scale)
            elif type == "CONE":
                bpy.ops.mesh.primitive_cone_add(location=location, rotation=rotation, scale=scale)
            elif type == "TORUS":
                bpy.ops.mesh.primitive_torus_add(
                    align=align,
                    location=location,
                    rotation=rotation,
                    major_segments=major_segments,
                    minor_segments=minor_segments,
                    mode=mode,
                    major_radius=major_radius,
                    minor_radius=minor_radius,
                    abso_major_rad=abso_major_rad,
                    abso_minor_rad=abso_minor_rad,
                    generate_uvs=generate_uvs
                )
            elif type == "EMPTY":
                bpy.ops.object.empty_add(location=location, rotation=rotation, scale=scale)
            elif type == "CAMERA":
                bpy.ops.object.camera_add(location=location, rotation=rotation)
            elif type == "LIGHT":
                bpy.ops.object.light_add(type='POINT', location=location,rotation=rotation, scale=scale)
            else:
                raise ValueError(f"Unsupported object type: {type}")
            
            # Force update the view layer
            bpy.context.view_layer.update()
            
            # Get the active object (which should be our newly created object)
            obj = bpy.context.view_layer.objects.active
            
            # If we don't have an active object, something went wrong
            if obj is None:
                raise RuntimeError("Failed to create object - no active object")
            
            # Make sure it's selected
            obj.select_set(True)
            
            # Rename if name is provided
            if name:
                obj.name = name
                if obj.data:
                    obj.data.name = name
            
            # Set rotation mode to XYZ
            obj.rotation_mode = 'XYZ'    
            
            if custom_properties is not None:
                if isinstance(custom_properties, str):
                    try:
                        custom_properties = json.loads(custom_properties)
                    except json.JSONDecodeError:
                        raise ValueError("custom_properties must be a valid JSON string")
                elif not isinstance(custom_properties, dict):
                    raise TypeError("custom_properties must be a dictionary or JSON string")
            
            initial_props = {
                "initialName": obj.name,
            }
            
            if custom_properties:
                if isinstance(custom_properties, str):
                    try:
                        custom_properties = json.loads(custom_properties)
                    except json.JSONDecodeError:
                        raise ValueError("custom_properties must be a valid JSON string")
                initial_props.update(custom_properties)
            
            for key, value in initial_props.items():
                if isinstance(value, (dict, list)):
                    obj[key] = json.dumps(value)
                else:
                    obj[key] = value 

            # Patch for PLANE: scale don't work with bpy.ops.mesh.primitive_plane_add()
            if type in {"PLANE"}:
                obj.scale = scale

            # Return the object info
            result = {
                "name": obj.name,
                "type": obj.type,
                "location": [obj.location.x, obj.location.y, obj.location.z],
                "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z],
                "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
                
            }
            
            if obj.type == "MESH":
                bounding_box = self._get_aabb(obj)
                result["world_bounding_box"] = bounding_box
            
            return result
        except Exception as e:
            print(f"Error in create_object: {str(e)}")
            traceback.print_exc()
            return {"error": str(e)}

    def modify_object(self, name, location=None,rotation_mode="XYZ",
                      rotation=None, scale=None, visible=None,
                      custom_properties=None):
        """Modify an existing object in the scene"""
        # Find the object by name
        obj = bpy.data.objects.get(name)
        if not obj:
            raise ValueError(f"Object not found: {name}")
        
        # Modify properties as requested
        if location is not None:
            obj.location = location
        
        if rotation is not None:
            obj.rotation_euler = rotation
        
        if scale is not None:
            obj.scale = scale
        
        if visible is not None:
            obj.hide_viewport = not visible
            obj.hide_render = not visible
        
        # Set rotation mode to XYZ
        obj.rotation_mode = 'XYZ'
       
        if custom_properties:
            if isinstance(custom_properties, str):
                try:
                    custom_properties = json.loads(custom_properties)
                except json.JSONDecodeError:
                    raise ValueError("custom_properties must be a valid JSON string")
            
            for key, value in custom_properties.items():
                if isinstance(value, (dict, list)):
                    obj[key] = json.dumps(value)
                else:
                    obj[key] = value
        obj["images"] = self.render_images((obj.location.x, obj.location.y, obj.location.z), name, 5)
        
        result = {
            "name": obj.name,
            "type": obj.type,
            "location": [obj.location.x, obj.location.y, obj.location.z],
            "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z],
            "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            "visible": obj.visible_get(),
            "images": obj["images"],
        }

        if obj.type == "MESH":
            bounding_box = self._get_aabb(obj)
            result["world_bounding_box"] = bounding_box
        return result

    def generate_duplicate_positions(self, obj_count, pattern, spacing, grid_size):
        """Generate positions for duplicated objects based on pattern"""
        positions = []
        if pattern == "line":
            for i in range(obj_count):
                positions.append((i * spacing[0], i * spacing[1], i * spacing[2]))
        
        elif pattern == "grid" and grid_size:
            rows, cols = grid_size
            for i in range(rows):
                for j in range(cols):
                    if len(positions) >= obj_count:
                        break
                    positions.append((j * spacing[0], i * spacing[1], 0))
        
        elif pattern == "circle":
            radius = spacing[0] if spacing else 1.0
            for i in range(obj_count):
                angle = 2 * pi * i / obj_count
                positions.append((radius * cos(angle), radius * sin(angle), 0))
        
        elif pattern == "random":
            for _ in range(obj_count):
                positions.append((
                    random.uniform(-spacing[0], spacing[0]) if spacing else random.uniform(-1, 1),
                    random.uniform(-spacing[1], spacing[1]) if spacing else random.uniform(-1, 1),
                    random.uniform(-spacing[2], spacing[2]) if spacing else random.uniform(-1, 1)
                ))
        
        else:
            positions = [(0, 0, 0)] * obj_count
        
        return positions
    
    def apply_duplicate_settings(self, new_obj, original_obj, position, random_rotation=False):
        """Apply position and rotation settings to duplicated object"""
        new_obj.location = (
            original_obj.location.x + position[0],
            original_obj.location.y + position[1],
            original_obj.location.z + position[2]
        )
        
        if random_rotation:
            new_obj.rotation_euler = (
                random.uniform(0, 2*pi),
                random.uniform(0, 2*pi),
                random.uniform(0, 2*pi)
            )
    
    def duplicate_single_object(self, obj, count, pattern, spacing, grid_size, random_rotation=False):
        """Handle duplication for a single object"""
        if spacing is None:
            spacing = [1.0, 1.0, 1.0]
        
        positions = self.generate_duplicate_positions(count, pattern, spacing, grid_size)
        new_names = []
        
        for i, pos in enumerate(positions):
            new_obj = obj.copy()
            new_obj.data = obj.data.copy()
            
            bpy.context.collection.objects.link(new_obj)
            new_obj.name = f"{obj.name}_copy_{i+1}"
            self.apply_duplicate_settings(new_obj, obj, pos, random_rotation)
            new_names.append(new_obj.name)
        
        return new_names
    
    def duplicate_multiple_objects(self, names, count, pattern, spacing, grid_size, random_rotation):
        """Handle duplication for multiple objects"""
        if spacing is None:
            spacing = [1.0, 1.0, 1.0]
        
        new_names = []
        for obj_name in names:
            obj = bpy.data.objects.get(obj_name)
            if not obj:
                raise ValueError(f"Object not found: {obj_name}")
            
            obj_new_names = self.duplicate_single_object(obj, count, pattern, spacing, grid_size, random_rotation)
            new_names.extend(obj_new_names)
        
        return new_names
    
    def duplicate_object(self, name, count=1, pattern=None, grid_size=None, spacing=None, random_rotation=False):
        """Main duplicate function that handles both single and multiple objects"""
        try:
            if isinstance(name, str):
                obj = bpy.data.objects.get(name)
                if not obj:
                    raise ValueError(f"Object not found: {name}")
                
                new_names = self.duplicate_single_object(obj, count, pattern, spacing, grid_size, random_rotation)
                return {"name": new_names} if count > 1 else {"name": new_names[0]}
            else:
                new_names = self.duplicate_multiple_objects(name, count, pattern, spacing, grid_size, random_rotation)
                return {"names": new_names}
        except Exception as e:
            print(f"Error in duplicate_object: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}
        
    def delete_object(self, name):
        """Delete an object from the scene"""
        obj = bpy.data.objects.get(name)
        if not obj:
            raise ValueError(f"Object not found: {name}")
        
        # Store the name to return
        obj_name = obj.name
        
        # Select and delete the object
        if obj:
            bpy.data.objects.remove(obj, do_unlink=True)
        
        return {"deleted": obj_name}
    
    def get_object_info(self, name):
        """Get detailed information about a specific object"""
        obj = bpy.data.objects.get(name)
        if not obj:
            raise ValueError(f"Object not found: {name}")
        
        nearby_objects = self.nearby_objects(name)
        
        # Basic object info
        obj_info = {
            "name": obj.name,
            "type": obj.type,
            "location": [obj.location.x, obj.location.y, obj.location.z],
            "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z],
            "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            "visible": obj.visible_get(),
            "materials": [],
            "nearby_objects": nearby_objects
        }

        if obj.type == "MESH":
            bounding_box = self._get_aabb(obj)
            obj_info["world_bounding_box"] = bounding_box
        
        # Add material slots
        for slot in obj.material_slots:
            if slot.material:
                obj_info["materials"].append(slot.material.name)
        
        # Add mesh data if applicable
        if obj.type == 'MESH' and obj.data:
            mesh = obj.data
            obj_info["mesh"] = {
                "vertices": len(mesh.vertices),
                "edges": len(mesh.edges),
                "polygons": len(mesh.polygons),
            }
        obj_info["images"] = self.render_images((obj.location.x, obj.location.y, obj.location.z),name,5)
        return obj_info
    
    def execute_code(self, code):
        """Execute arbitrary Blender Python code"""
        # This is powerful but potentially dangerous - use with caution
        try:
            # Create a local namespace for execution
            namespace = {"bpy": bpy}
            exec(code, namespace)
            return {"executed": True}
        except Exception as e:
            raise Exception(f"Code execution error: {str(e)}")
    
    def set_material(self, object_name, material_name=None, create_if_missing=True, color=None):
        """Set or create a material for an object"""
        try:
            # Get the object
            obj = bpy.data.objects.get(object_name)
            if not obj:
                raise ValueError(f"Object not found: {object_name}")
            
            # Make sure object can accept materials
            if not hasattr(obj, 'data') or not hasattr(obj.data, 'materials'):
                raise ValueError(f"Object {object_name} cannot accept materials")
            
            # Create or get material
            if material_name:
                mat = bpy.data.materials.get(material_name)
                if not mat and create_if_missing:
                    mat = bpy.data.materials.new(name=material_name)
                    print(f"Created new material: {material_name}")
            else:
                # Generate unique material name if none provided
                mat_name = f"{object_name}_material"
                mat = bpy.data.materials.get(mat_name)
                if not mat:
                    mat = bpy.data.materials.new(name=mat_name)
                material_name = mat_name
                print(f"Using material: {mat_name}")
            
            # Set up material nodes if needed
            if mat:
                if not mat.use_nodes:
                    mat.use_nodes = True
                
                # Get or create Principled BSDF
                principled = mat.node_tree.nodes.get('Principled BSDF')
                if not principled:
                    principled = mat.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
                    # Get or create Material Output
                    output = mat.node_tree.nodes.get('Material Output')
                    if not output:
                        output = mat.node_tree.nodes.new('ShaderNodeOutputMaterial')
                    # Link if not already linked
                    if not principled.outputs[0].links:
                        mat.node_tree.links.new(principled.outputs[0], output.inputs[0])
                
                # Set color if provided
                if color and len(color) >= 3:
                    principled.inputs['Base Color'].default_value = (
                        color[0],
                        color[1],
                        color[2],
                        1.0 if len(color) < 4 else color[3]
                    )
                    print(f"Set material color to {color}")
            
            # Assign material to object if not already assigned
            if mat:
                if not obj.data.materials:
                    obj.data.materials.append(mat)
                else:
                    # Only modify first material slot
                    obj.data.materials[0] = mat
                
                print(f"Assigned material {mat.name} to object {object_name}")
                
                return {
                    "status": "success",
                    "object": object_name,
                    "material": mat.name,
                    "color": color if color else None
                }
            else:
                raise ValueError(f"Failed to create or find material: {material_name}")
            
        except Exception as e:
            print(f"Error in set_material: {str(e)}")
            traceback.print_exc()
            return {
                "status": "error",
                "message": str(e),
                "object": object_name,
                "material": material_name if 'material_name' in locals() else None
            }
    
    def render_scene(self, output_path=None, resolution_x=None, resolution_y=None):
        """Render the current scene"""
        if resolution_x is not None:
            bpy.context.scene.render.resolution_x = resolution_x
        
        if resolution_y is not None:
            bpy.context.scene.render.resolution_y = resolution_y
        
        if output_path:
            bpy.context.scene.render.filepath = output_path
        
        # Render the scene
        bpy.ops.render.render(write_still=bool(output_path))
        
        return {
            "rendered": True,
            "output_path": output_path if output_path else "[not saved]",
            "resolution": [bpy.context.scene.render.resolution_x, bpy.context.scene.render.resolution_y],
        }

    def get_polyhaven_categories(self, asset_type):
        """Get categories for a specific asset type from Polyhaven"""
        try:
            if asset_type not in ["hdris", "textures", "models", "all"]:
                return {"error": f"Invalid asset type: {asset_type}. Must be one of: hdris, textures, models, all"}
                
            response = requests.get(f"https://api.polyhaven.com/categories/{asset_type}")
            if response.status_code == 200:
                return {"categories": response.json()}
            else:
                return {"error": f"API request failed with status code {response.status_code}"}
        except Exception as e:
            return {"error": str(e)}
    
    def search_polyhaven_assets(self, asset_type=None, categories=None):
        """Search for assets from Polyhaven with optional filtering"""
        try:
            url = "https://api.polyhaven.com/assets"
            params = {}
            
            if asset_type and asset_type != "all":
                if asset_type not in ["hdris", "textures", "models"]:
                    return {"error": f"Invalid asset type: {asset_type}. Must be one of: hdris, textures, models, all"}
                params["type"] = asset_type
                
            if categories:
                params["categories"] = categories
                
            response = requests.get(url, params=params)
            if response.status_code == 200:
                # Limit the response size to avoid overwhelming Blender
                assets = response.json()
                # Return only the first 20 assets to keep response size manageable
                limited_assets = {}
                for i, (key, value) in enumerate(assets.items()):
                    if i >= 20:  # Limit to 20 assets
                        break
                    limited_assets[key] = value
                
                return {"assets": limited_assets, "total_count": len(assets), "returned_count": len(limited_assets)}
            else:
                return {"error": f"API request failed with status code {response.status_code}"}
        except Exception as e:
            return {"error": str(e)}
    
    def download_polyhaven_asset(self, asset_id, asset_type, resolution="1k", file_format=None):
        try:
            # First get the files information
            files_response = requests.get(f"https://api.polyhaven.com/files/{asset_id}")
            if files_response.status_code != 200:
                return {"error": f"Failed to get asset files: {files_response.status_code}"}
            
            files_data = files_response.json()
            
            # Handle different asset types
            if asset_type == "hdris":
                # For HDRIs, download the .hdr or .exr file
                if not file_format:
                    file_format = "hdr"  # Default format for HDRIs
                
                if "hdri" in files_data and resolution in files_data["hdri"] and file_format in files_data["hdri"][resolution]:
                    file_info = files_data["hdri"][resolution][file_format]
                    file_url = file_info["url"]
                    
                    # For HDRIs, we need to save to a temporary file first
                    # since Blender can't properly load HDR data directly from memory
                    with tempfile.NamedTemporaryFile(suffix=f".{file_format}", delete=False) as tmp_file:
                        # Download the file
                        response = requests.get(file_url)
                        if response.status_code != 200:
                            return {"error": f"Failed to download HDRI: {response.status_code}"}
                        
                        tmp_file.write(response.content)
                        tmp_path = tmp_file.name
                    
                    try:
                        # Create a new world if none exists
                        if not bpy.data.worlds:
                            bpy.data.worlds.new("World")
                        
                        world = bpy.data.worlds[0]
                        world.use_nodes = True
                        node_tree = world.node_tree
                        
                        # Clear existing nodes
                        for node in node_tree.nodes:
                            node_tree.nodes.remove(node)
                        
                        # Create nodes
                        tex_coord = node_tree.nodes.new(type='ShaderNodeTexCoord')
                        tex_coord.location = (-800, 0)
                        
                        mapping = node_tree.nodes.new(type='ShaderNodeMapping')
                        mapping.location = (-600, 0)
                        
                        # Load the image from the temporary file
                        env_tex = node_tree.nodes.new(type='ShaderNodeTexEnvironment')
                        env_tex.location = (-400, 0)
                        env_tex.image = bpy.data.images.load(tmp_path)
                        
                        # Use a color space that exists in all Blender versions
                        if file_format.lower() == 'exr':
                            # Try to use Linear color space for EXR files
                            try:
                                env_tex.image.colorspace_settings.name = 'Linear'
                            except:
                                # Fallback to Non-Color if Linear isn't available
                                env_tex.image.colorspace_settings.name = 'Non-Color'
                        else:  # hdr
                            # For HDR files, try these options in order
                            for color_space in ['Linear', 'Linear Rec.709', 'Non-Color']:
                                try:
                                    env_tex.image.colorspace_settings.name = color_space
                                    break  # Stop if we successfully set a color space
                                except:
                                    continue
                        
                        background = node_tree.nodes.new(type='ShaderNodeBackground')
                        background.location = (-200, 0)
                        
                        output = node_tree.nodes.new(type='ShaderNodeOutputWorld')
                        output.location = (0, 0)
                        
                        # Connect nodes
                        node_tree.links.new(tex_coord.outputs['Generated'], mapping.inputs['Vector'])
                        node_tree.links.new(mapping.outputs['Vector'], env_tex.inputs['Vector'])
                        node_tree.links.new(env_tex.outputs['Color'], background.inputs['Color'])
                        node_tree.links.new(background.outputs['Background'], output.inputs['Surface'])
                        
                        # Set as active world
                        bpy.context.scene.world = world
                        
                        # Clean up temporary file
                        try:
                            tempfile._cleanup()  # This will clean up all temporary files
                        except:
                            pass
                        
                        return {
                            "success": True, 
                            "message": f"HDRI {asset_id} imported successfully",
                            "image_name": env_tex.image.name
                        }
                    except Exception as e:
                        return {"error": f"Failed to set up HDRI in Blender: {str(e)}"}
                else:
                    return {"error": f"Requested resolution or format not available for this HDRI"}
                    
            elif asset_type == "textures":
                if not file_format:
                    file_format = "jpg"  # Default format for textures
                
                downloaded_maps = {}
                
                try:
                    for map_type in files_data:
                        if map_type not in ["blend", "gltf"]:  # Skip non-texture files
                            if resolution in files_data[map_type] and file_format in files_data[map_type][resolution]:
                                file_info = files_data[map_type][resolution][file_format]
                                file_url = file_info["url"]
                                
                                # Use NamedTemporaryFile like we do for HDRIs
                                with tempfile.NamedTemporaryFile(suffix=f".{file_format}", delete=False) as tmp_file:
                                    # Download the file
                                    response = requests.get(file_url)
                                    if response.status_code == 200:
                                        tmp_file.write(response.content)
                                        tmp_path = tmp_file.name
                                        
                                        # Load image from temporary file
                                        image = bpy.data.images.load(tmp_path)
                                        image.name = f"{asset_id}_{map_type}.{file_format}"
                                        
                                        # Pack the image into .blend file
                                        image.pack()
                                        
                                        # Set color space based on map type
                                        if map_type in ['color', 'diffuse', 'albedo']:
                                            try:
                                                image.colorspace_settings.name = 'sRGB'
                                            except:
                                                pass
                                        else:
                                            try:
                                                image.colorspace_settings.name = 'Non-Color'
                                            except:
                                                pass
                                        
                                        downloaded_maps[map_type] = image
                                        
                                        # Clean up temporary file
                                        try:
                                            os.unlink(tmp_path)
                                        except:
                                            pass
                
                    if not downloaded_maps:
                        return {"error": f"No texture maps found for the requested resolution and format"}
                    
                    # Create a new material with the downloaded textures
                    mat = bpy.data.materials.new(name=asset_id)
                    mat.use_nodes = True
                    nodes = mat.node_tree.nodes
                    links = mat.node_tree.links
                    
                    # Clear default nodes
                    for node in nodes:
                        nodes.remove(node)
                    
                    # Create output node
                    output = nodes.new(type='ShaderNodeOutputMaterial')
                    output.location = (300, 0)
                    
                    # Create principled BSDF node
                    principled = nodes.new(type='ShaderNodeBsdfPrincipled')
                    principled.location = (0, 0)
                    links.new(principled.outputs[0], output.inputs[0])
                    
                    # Add texture nodes based on available maps
                    tex_coord = nodes.new(type='ShaderNodeTexCoord')
                    tex_coord.location = (-800, 0)
                    
                    mapping = nodes.new(type='ShaderNodeMapping')
                    mapping.location = (-600, 0)
                    mapping.vector_type = 'TEXTURE'  # Changed from default 'POINT' to 'TEXTURE'
                    links.new(tex_coord.outputs['UV'], mapping.inputs['Vector'])
                    
                    # Position offset for texture nodes
                    x_pos = -400
                    y_pos = 300
                    
                    # Connect different texture maps
                    for map_type, image in downloaded_maps.items():
                        tex_node = nodes.new(type='ShaderNodeTexImage')
                        tex_node.location = (x_pos, y_pos)
                        tex_node.image = image
                        
                        # Set color space based on map type
                        if map_type.lower() in ['color', 'diffuse', 'albedo']:
                            try:
                                tex_node.image.colorspace_settings.name = 'sRGB'
                            except:
                                pass  # Use default if sRGB not available
                        else:
                            try:
                                tex_node.image.colorspace_settings.name = 'Non-Color'
                            except:
                                pass  # Use default if Non-Color not available
                        
                        links.new(mapping.outputs['Vector'], tex_node.inputs['Vector'])
                        
                        # Connect to appropriate input on Principled BSDF
                        if map_type.lower() in ['color', 'diffuse', 'albedo']:
                            links.new(tex_node.outputs['Color'], principled.inputs['Base Color'])
                        elif map_type.lower() in ['roughness', 'rough']:
                            links.new(tex_node.outputs['Color'], principled.inputs['Roughness'])
                        elif map_type.lower() in ['metallic', 'metalness', 'metal']:
                            links.new(tex_node.outputs['Color'], principled.inputs['Metallic'])
                        elif map_type.lower() in ['normal', 'nor']:
                            # Add normal map node
                            normal_map = nodes.new(type='ShaderNodeNormalMap')
                            normal_map.location = (x_pos + 200, y_pos)
                            links.new(tex_node.outputs['Color'], normal_map.inputs['Color'])
                            links.new(normal_map.outputs['Normal'], principled.inputs['Normal'])
                        elif map_type in ['displacement', 'disp', 'height']:
                            # Add displacement node
                            disp_node = nodes.new(type='ShaderNodeDisplacement')
                            disp_node.location = (x_pos + 200, y_pos - 200)
                            links.new(tex_node.outputs['Color'], disp_node.inputs['Height'])
                            links.new(disp_node.outputs['Displacement'], output.inputs['Displacement'])
                        
                        y_pos -= 250
                    
                    return {
                        "success": True, 
                        "message": f"Texture {asset_id} imported as material",
                        "material": mat.name,
                        "maps": list(downloaded_maps.keys())
                    }
                
                except Exception as e:
                    return {"error": f"Failed to process textures: {str(e)}"}
                
            elif asset_type == "models":
                # For models, prefer glTF format if available
                if not file_format:
                    file_format = "gltf"  # Default format for models
                
                if file_format in files_data and resolution in files_data[file_format]:
                    file_info = files_data[file_format][resolution][file_format]
                    file_url = file_info["url"]
                    
                    # Create a temporary directory to store the model and its dependencies
                    temp_dir = tempfile.mkdtemp()
                    main_file_path = ""
                    
                    try:
                        # Download the main model file
                        main_file_name = file_url.split("/")[-1]
                        main_file_path = os.path.join(temp_dir, main_file_name)
                        
                        response = requests.get(file_url)
                        if response.status_code != 200:
                            return {"error": f"Failed to download model: {response.status_code}"}
                        
                        with open(main_file_path, "wb") as f:
                            f.write(response.content)
                        
                        # Check for included files and download them
                        if "include" in file_info and file_info["include"]:
                            for include_path, include_info in file_info["include"].items():
                                # Get the URL for the included file - this is the fix
                                include_url = include_info["url"]
                                
                                # Create the directory structure for the included file
                                include_file_path = os.path.join(temp_dir, include_path)
                                os.makedirs(os.path.dirname(include_file_path), exist_ok=True)
                                
                                # Download the included file
                                include_response = requests.get(include_url)
                                if include_response.status_code == 200:
                                    with open(include_file_path, "wb") as f:
                                        f.write(include_response.content)
                                else:
                                    print(f"Failed to download included file: {include_path}")
                        
                        # Import the model into Blender
                        if file_format == "gltf" or file_format == "glb":
                            bpy.ops.import_scene.gltf(filepath=main_file_path)
                        elif file_format == "fbx":
                            bpy.ops.import_scene.fbx(filepath=main_file_path)
                        elif file_format == "obj":
                            bpy.ops.import_scene.obj(filepath=main_file_path)
                        elif file_format == "blend":
                            # For blend files, we need to append or link
                            with bpy.data.libraries.load(main_file_path, link=False) as (data_from, data_to):
                                data_to.objects = data_from.objects
                            
                            # Link the objects to the scene
                            for obj in data_to.objects:
                                if obj is not None:
                                    bpy.context.collection.objects.link(obj)
                        else:
                            return {"error": f"Unsupported model format: {file_format}"}
                        
                        # Get the names of imported objects
                        imported_objects = [obj.name for obj in bpy.context.selected_objects]
                        
                        return {
                            "success": True, 
                            "message": f"Model {asset_id} imported successfully",
                            "imported_objects": imported_objects
                        }
                    except Exception as e:
                        return {"error": f"Failed to import model: {str(e)}"}
                    finally:
                        # Clean up temporary directory
                        try:
                            shutil.rmtree(temp_dir)
                        except:
                            print(f"Failed to clean up temporary directory: {temp_dir}")
                else:
                    return {"error": f"Requested format or resolution not available for this model"}
                
            else:
                return {"error": f"Unsupported asset type: {asset_type}"}
                
        except Exception as e:
            return {"error": f"Failed to download asset: {str(e)}"}

    def set_texture(self, object_name, texture_id):
        """Apply a previously downloaded Polyhaven texture to an object by creating a new material"""
        try:
            # Get the object
            obj = bpy.data.objects.get(object_name)
            if not obj:
                return {"error": f"Object not found: {object_name}"}
            
            # Make sure object can accept materials
            if not hasattr(obj, 'data') or not hasattr(obj.data, 'materials'):
                return {"error": f"Object {object_name} cannot accept materials"}
            
            # Find all images related to this texture and ensure they're properly loaded
            texture_images = {}
            for img in bpy.data.images:
                if img.name.startswith(texture_id + "_"):
                    # Extract the map type from the image name
                    map_type = img.name.split('_')[-1].split('.')[0]
                    
                    # Force a reload of the image
                    img.reload()
                    
                    # Ensure proper color space
                    if map_type.lower() in ['color', 'diffuse', 'albedo']:
                        try:
                            img.colorspace_settings.name = 'sRGB'
                        except:
                            pass
                    else:
                        try:
                            img.colorspace_settings.name = 'Non-Color'
                        except:
                            pass
                    
                    # Ensure the image is packed
                    if not img.packed_file:
                        img.pack()
                    
                    texture_images[map_type] = img
                    print(f"Loaded texture map: {map_type} - {img.name}")
                    
                    # Debug info
                    print(f"Image size: {img.size[0]}x{img.size[1]}")
                    print(f"Color space: {img.colorspace_settings.name}")
                    print(f"File format: {img.file_format}")
                    print(f"Is packed: {bool(img.packed_file)}")

            if not texture_images:
                return {"error": f"No texture images found for: {texture_id}. Please download the texture first."}
            
            # Create a new material
            new_mat_name = f"{texture_id}_material_{object_name}"
            
            # Remove any existing material with this name to avoid conflicts
            existing_mat = bpy.data.materials.get(new_mat_name)
            if existing_mat:
                bpy.data.materials.remove(existing_mat)
            
            new_mat = bpy.data.materials.new(name=new_mat_name)
            new_mat.use_nodes = True
            
            # Set up the material nodes
            nodes = new_mat.node_tree.nodes
            links = new_mat.node_tree.links
            
            # Clear default nodes
            nodes.clear()
            
            # Create output node
            output = nodes.new(type='ShaderNodeOutputMaterial')
            output.location = (600, 0)
            
            # Create principled BSDF node
            principled = nodes.new(type='ShaderNodeBsdfPrincipled')
            principled.location = (300, 0)
            links.new(principled.outputs[0], output.inputs[0])
            
            # Add texture nodes based on available maps
            tex_coord = nodes.new(type='ShaderNodeTexCoord')
            tex_coord.location = (-800, 0)
            
            mapping = nodes.new(type='ShaderNodeMapping')
            mapping.location = (-600, 0)
            mapping.vector_type = 'TEXTURE'  # Changed from default 'POINT' to 'TEXTURE'
            links.new(tex_coord.outputs['UV'], mapping.inputs['Vector'])
            
            # Position offset for texture nodes
            x_pos = -400
            y_pos = 300
            
            # Connect different texture maps
            for map_type, image in texture_images.items():
                tex_node = nodes.new(type='ShaderNodeTexImage')
                tex_node.location = (x_pos, y_pos)
                tex_node.image = image
                
                # Set color space based on map type
                if map_type.lower() in ['color', 'diffuse', 'albedo']:
                    try:
                        tex_node.image.colorspace_settings.name = 'sRGB'
                    except:
                        pass  # Use default if sRGB not available
                else:
                    try:
                        tex_node.image.colorspace_settings.name = 'Non-Color'
                    except:
                        pass  # Use default if Non-Color not available
                
                links.new(mapping.outputs['Vector'], tex_node.inputs['Vector'])
                
                # Connect to appropriate input on Principled BSDF
                if map_type.lower() in ['color', 'diffuse', 'albedo']:
                    links.new(tex_node.outputs['Color'], principled.inputs['Base Color'])
                elif map_type.lower() in ['roughness', 'rough']:
                    links.new(tex_node.outputs['Color'], principled.inputs['Roughness'])
                elif map_type.lower() in ['metallic', 'metalness', 'metal']:
                    links.new(tex_node.outputs['Color'], principled.inputs['Metallic'])
                elif map_type.lower() in ['normal', 'nor', 'dx', 'gl']:
                    # Add normal map node
                    normal_map = nodes.new(type='ShaderNodeNormalMap')
                    normal_map.location = (x_pos + 200, y_pos)
                    links.new(tex_node.outputs['Color'], normal_map.inputs['Color'])
                    links.new(normal_map.outputs['Normal'], principled.inputs['Normal'])
                elif map_type.lower() in ['displacement', 'disp', 'height']:
                    # Add displacement node
                    disp_node = nodes.new(type='ShaderNodeDisplacement')
                    disp_node.location = (x_pos + 200, y_pos - 200)
                    disp_node.inputs['Scale'].default_value = 0.1  # Reduce displacement strength
                    links.new(tex_node.outputs['Color'], disp_node.inputs['Height'])
                    links.new(disp_node.outputs['Displacement'], output.inputs['Displacement'])
                
                y_pos -= 250
            
            # Second pass: Connect nodes with proper handling for special cases
            texture_nodes = {}
            
            # First find all texture nodes and store them by map type
            for node in nodes:
                if node.type == 'TEX_IMAGE' and node.image:
                    for map_type, image in texture_images.items():
                        if node.image == image:
                            texture_nodes[map_type] = node
                            break
            
            # Now connect everything using the nodes instead of images
            # Handle base color (diffuse)
            for map_name in ['color', 'diffuse', 'albedo']:
                if map_name in texture_nodes:
                    links.new(texture_nodes[map_name].outputs['Color'], principled.inputs['Base Color'])
                    print(f"Connected {map_name} to Base Color")
                    break
            
            # Handle roughness
            for map_name in ['roughness', 'rough']:
                if map_name in texture_nodes:
                    links.new(texture_nodes[map_name].outputs['Color'], principled.inputs['Roughness'])
                    print(f"Connected {map_name} to Roughness")
                    break
            
            # Handle metallic
            for map_name in ['metallic', 'metalness', 'metal']:
                if map_name in texture_nodes:
                    links.new(texture_nodes[map_name].outputs['Color'], principled.inputs['Metallic'])
                    print(f"Connected {map_name} to Metallic")
                    break
            
            # Handle normal maps
            for map_name in ['gl', 'dx', 'nor']:
                if map_name in texture_nodes:
                    normal_map_node = nodes.new(type='ShaderNodeNormalMap')
                    normal_map_node.location = (100, 100)
                    links.new(texture_nodes[map_name].outputs['Color'], normal_map_node.inputs['Color'])
                    links.new(normal_map_node.outputs['Normal'], principled.inputs['Normal'])
                    print(f"Connected {map_name} to Normal")
                    break
            
            # Handle displacement
            for map_name in ['displacement', 'disp', 'height']:
                if map_name in texture_nodes:
                    disp_node = nodes.new(type='ShaderNodeDisplacement')
                    disp_node.location = (300, -200)
                    disp_node.inputs['Scale'].default_value = 0.1  # Reduce displacement strength
                    links.new(texture_nodes[map_name].outputs['Color'], disp_node.inputs['Height'])
                    links.new(disp_node.outputs['Displacement'], output.inputs['Displacement'])
                    print(f"Connected {map_name} to Displacement")
                    break
            
            # Handle ARM texture (Ambient Occlusion, Roughness, Metallic)
            if 'arm' in texture_nodes:
                separate_rgb = nodes.new(type='ShaderNodeSeparateRGB')
                separate_rgb.location = (-200, -100)
                links.new(texture_nodes['arm'].outputs['Color'], separate_rgb.inputs['Image'])
                
                # Connect Roughness (G) if no dedicated roughness map
                if not any(map_name in texture_nodes for map_name in ['roughness', 'rough']):
                    links.new(separate_rgb.outputs['G'], principled.inputs['Roughness'])
                    print("Connected ARM.G to Roughness")
                
                # Connect Metallic (B) if no dedicated metallic map
                if not any(map_name in texture_nodes for map_name in ['metallic', 'metalness', 'metal']):
                    links.new(separate_rgb.outputs['B'], principled.inputs['Metallic'])
                    print("Connected ARM.B to Metallic")
                
                # For AO (R channel), multiply with base color if we have one
                base_color_node = None
                for map_name in ['color', 'diffuse', 'albedo']:
                    if map_name in texture_nodes:
                        base_color_node = texture_nodes[map_name]
                        break
                
                if base_color_node:
                    mix_node = nodes.new(type='ShaderNodeMixRGB')
                    mix_node.location = (100, 200)
                    mix_node.blend_type = 'MULTIPLY'
                    mix_node.inputs['Fac'].default_value = 0.8  # 80% influence
                    
                    # Disconnect direct connection to base color
                    for link in base_color_node.outputs['Color'].links:
                        if link.to_socket == principled.inputs['Base Color']:
                            links.remove(link)
                    
                    # Connect through the mix node
                    links.new(base_color_node.outputs['Color'], mix_node.inputs[1])
                    links.new(separate_rgb.outputs['R'], mix_node.inputs[2])
                    links.new(mix_node.outputs['Color'], principled.inputs['Base Color'])
                    print("Connected ARM.R to AO mix with Base Color")
            
            # Handle AO (Ambient Occlusion) if separate
            if 'ao' in texture_nodes:
                base_color_node = None
                for map_name in ['color', 'diffuse', 'albedo']:
                    if map_name in texture_nodes:
                        base_color_node = texture_nodes[map_name]
                        break
                
                if base_color_node:
                    mix_node = nodes.new(type='ShaderNodeMixRGB')
                    mix_node.location = (100, 200)
                    mix_node.blend_type = 'MULTIPLY'
                    mix_node.inputs['Fac'].default_value = 0.8  # 80% influence
                    
                    # Disconnect direct connection to base color
                    for link in base_color_node.outputs['Color'].links:
                        if link.to_socket == principled.inputs['Base Color']:
                            links.remove(link)
                    
                    # Connect through the mix node
                    links.new(base_color_node.outputs['Color'], mix_node.inputs[1])
                    links.new(texture_nodes['ao'].outputs['Color'], mix_node.inputs[2])
                    links.new(mix_node.outputs['Color'], principled.inputs['Base Color'])
                    print("Connected AO to mix with Base Color")
            
            # CRITICAL: Make sure to clear all existing materials from the object
            while len(obj.data.materials) > 0:
                obj.data.materials.pop(index=0)
            
            # Assign the new material to the object
            obj.data.materials.append(new_mat)
            
            # CRITICAL: Make the object active and select it
            bpy.context.view_layer.objects.active = obj
            obj.select_set(True)
            
            # CRITICAL: Force Blender to update the material
            bpy.context.view_layer.update()
            
            # Get the list of texture maps
            texture_maps = list(texture_images.keys())
            
            # Get info about texture nodes for debugging
            material_info = {
                "name": new_mat.name,
                "has_nodes": new_mat.use_nodes,
                "node_count": len(new_mat.node_tree.nodes),
                "texture_nodes": []
            }
            
            for node in new_mat.node_tree.nodes:
                if node.type == 'TEX_IMAGE' and node.image:
                    connections = []
                    for output in node.outputs:
                        for link in output.links:
                            connections.append(f"{output.name} → {link.to_node.name}.{link.to_socket.name}")
                    
                    material_info["texture_nodes"].append({
                        "name": node.name,
                        "image": node.image.name,
                        "colorspace": node.image.colorspace_settings.name,
                        "connections": connections
                    })
            
            return {
                "success": True,
                "message": f"Created new material and applied texture {texture_id} to {object_name}",
                "material": new_mat.name,
                "maps": texture_maps,
                "material_info": material_info
            }
            
        except Exception as e:
            print(f"Error in set_texture: {str(e)}")
            traceback.print_exc()
            return {"error": f"Failed to apply texture: {str(e)}"}

    def get_polyhaven_status(self):
        """Get the current status of PolyHaven integration"""
        enabled = bpy.context.scene.blendermcp_use_polyhaven
        if enabled:
            return {"enabled": True, "message": "PolyHaven integration is enabled and ready to use."}
        else:
            return {
                "enabled": False, 
                "message": """PolyHaven integration is currently disabled. To enable it:
                            1. In the 3D Viewport, find the BlenderMCP panel in the sidebar (press N if hidden)
                            2. Check the 'Use assets from Poly Haven' checkbox
                            3. Restart the connection to Claude"""
        }

    #region Hyper3D
    def get_hyper3d_status(self):
        """Get the current status of Hyper3D Rodin integration"""
        enabled = bpy.context.scene.blendermcp_use_hyper3d
        if enabled:
            if not bpy.context.scene.blendermcp_hyper3d_api_key:
                return {
                    "enabled": False, 
                    "message": """Hyper3D Rodin integration is currently enabled, but API key is not given. To enable it:
                                1. In the 3D Viewport, find the BlenderMCP panel in the sidebar (press N if hidden)
                                2. Keep the 'Use Hyper3D Rodin 3D model generation' checkbox checked
                                3. Choose the right plaform and fill in the API Key
                                4. Restart the connection to Claude"""
                }
            mode = bpy.context.scene.blendermcp_hyper3d_mode
            message = f"Hyper3D Rodin integration is enabled and ready to use. Mode: {mode}. " + \
                f"Key type: {'private' if bpy.context.scene.blendermcp_hyper3d_api_key != RODIN_FREE_TRIAL_KEY else 'free_trial'}"
            return {
                "enabled": True,
                "message": message
            }
        else:
            return {
                "enabled": False, 
                "message": """Hyper3D Rodin integration is currently disabled. To enable it:
                            1. In the 3D Viewport, find the BlenderMCP panel in the sidebar (press N if hidden)
                            2. Check the 'Use Hyper3D Rodin 3D model generation' checkbox
                            3. Restart the connection to Claude"""
            }

    def create_rodin_job(self, *args, **kwargs):
        match bpy.context.scene.blendermcp_hyper3d_mode:
            case "MAIN_SITE":
                return self.create_rodin_job_main_site(*args, **kwargs)
            case "FAL_AI":
                return self.create_rodin_job_fal_ai(*args, **kwargs)
            case _:
                return f"Error: Unknown Hyper3D Rodin mode!"

    def create_rodin_job_main_site(
            self,
            text_prompt: str=None,
            images: list[tuple[str, str]]=None,
            bbox_condition=None
        ):
        try:
            if images is None:
                images = []
            """Call Rodin API, get the job uuid and subscription key"""
            files = [
                *[("images", (f"{i:04d}{img_suffix}", img)) for i, (img_suffix, img) in enumerate(images)],
                ("tier", (None, "Sketch")),
                ("mesh_mode", (None, "Raw")),
            ]
            if text_prompt:
                files.append(("prompt", (None, text_prompt)))
            if bbox_condition:
                files.append(("bbox_condition", (None, json.dumps(bbox_condition))))
            response = requests.post(
                "https://hyperhuman.deemos.com/api/v2/rodin",
                headers={
                    "Authorization": f"Bearer {bpy.context.scene.blendermcp_hyper3d_api_key}",
                },
                files=files
            )
            data = response.json()
            return data
        except Exception as e:
            return {"error": str(e)}
    
    def create_rodin_job_fal_ai(
            self,
            text_prompt: str=None,
            images: list[tuple[str, str]]=None,
            bbox_condition=None
        ):
        try:
            req_data = {
                "tier": "Sketch",
            }
            if images:
                req_data["input_image_urls"] = images
            if text_prompt:
                req_data["prompt"] = text_prompt
            if bbox_condition:
                req_data["bbox_condition"] = bbox_condition
            response = requests.post(
                "https://queue.fal.run/fal-ai/hyper3d/rodin",
                headers={
                    "Authorization": f"Key {bpy.context.scene.blendermcp_hyper3d_api_key}",
                    "Content-Type": "application/json",
                },
                json=req_data
            )
            data = response.json()
            return data
        except Exception as e:
            return {"error": str(e)}

    def poll_rodin_job_status(self, *args, **kwargs):
        match bpy.context.scene.blendermcp_hyper3d_mode:
            case "MAIN_SITE":
                return self.poll_rodin_job_status_main_site(*args, **kwargs)
            case "FAL_AI":
                return self.poll_rodin_job_status_fal_ai(*args, **kwargs)
            case _:
                return f"Error: Unknown Hyper3D Rodin mode!"

    def poll_rodin_job_status_main_site(self, subscription_key: str):
        """Call the job status API to get the job status"""
        response = requests.post(
            "https://hyperhuman.deemos.com/api/v2/status",
            headers={
                "Authorization": f"Bearer {bpy.context.scene.blendermcp_hyper3d_api_key}",
            },
            json={
                "subscription_key": subscription_key,
            },
        )
        data = response.json()
        return {
            "status_list": [i["status"] for i in data["jobs"]]
        }
    
    def poll_rodin_job_status_fal_ai(self, request_id: str):
        """Call the job status API to get the job status"""
        response = requests.get(
            f"https://queue.fal.run/fal-ai/hyper3d/requests/{request_id}/status",
            headers={
                "Authorization": f"KEY {bpy.context.scene.blendermcp_hyper3d_api_key}",
            },
        )
        data = response.json()
        return data

    @staticmethod
    def _clean_imported_glb(filepath, mesh_name=None):
        # Get the set of existing objects before import
        existing_objects = set(bpy.data.objects)

        # Import the GLB file
        bpy.ops.import_scene.gltf(filepath=filepath)
        
        # Ensure the context is updated
        bpy.context.view_layer.update()
        
        # Get all imported objects
        imported_objects = list(set(bpy.data.objects) - existing_objects)
        # imported_objects = [obj for obj in bpy.context.view_layer.objects if obj.select_get()]
        
        if not imported_objects:
            print("Error: No objects were imported.")
            return
        
        # Identify the mesh object
        mesh_obj = None
        
        if len(imported_objects) == 1 and imported_objects[0].type == 'MESH':
            mesh_obj = imported_objects[0]
            print("Single mesh imported, no cleanup needed.")
        else:
            if len(imported_objects) == 2:
                empty_objs = [i for i in imported_objects if i.type == "EMPTY"]
                if len(empty_objs) != 1:
                    print("Error: Expected an empty node with one mesh child or a single mesh object.")
                    return
                parent_obj = empty_objs.pop()
                if len(parent_obj.children) == 1:
                    potential_mesh = parent_obj.children[0]
                    if potential_mesh.type == 'MESH':
                        print("GLB structure confirmed: Empty node with one mesh child.")
                        
                        # Unparent the mesh from the empty node
                        potential_mesh.parent = None
                        
                        # Remove the empty node
                        bpy.data.objects.remove(parent_obj)
                        print("Removed empty node, keeping only the mesh.")
                        
                        mesh_obj = potential_mesh
                    else:
                        print("Error: Child is not a mesh object.")
                        return
                else:
                    print("Error: Expected an empty node with one mesh child or a single mesh object.")
                    return
            else:
                print("Error: Expected an empty node with one mesh child or a single mesh object.")
                return
        
        # Rename the mesh if needed
        try:
            if mesh_obj and mesh_obj.name is not None and mesh_name:
                mesh_obj.name = mesh_name
                if mesh_obj.data.name is not None:
                    mesh_obj.data.name = mesh_name
                print(f"Mesh renamed to: {mesh_name}")
        except Exception as e:
            print("Having issue with renaming, give up renaming.")

        return mesh_obj

    def import_generated_asset(self, *args, **kwargs):
        match bpy.context.scene.blendermcp_hyper3d_mode:
            case "MAIN_SITE":
                return self.import_generated_asset_main_site(*args, **kwargs)
            case "FAL_AI":
                return self.import_generated_asset_fal_ai(*args, **kwargs)
            case _:
                return f"Error: Unknown Hyper3D Rodin mode!"

    def import_generated_asset_main_site(self, task_uuid: str, name: str):
        """Fetch the generated asset, import into blender"""
        response = requests.post(
            "https://hyperhuman.deemos.com/api/v2/download",
            headers={
                "Authorization": f"Bearer {bpy.context.scene.blendermcp_hyper3d_api_key}",
            },
            json={
                'task_uuid': task_uuid
            }
        )
        data_ = response.json()
        temp_file = None
        for i in data_["list"]:
            if i["name"].endswith(".glb"):
                temp_file = tempfile.NamedTemporaryFile(
                    delete=False,
                    prefix=task_uuid,
                    suffix=".glb",
                )
    
                try:
                    # Download the content
                    response = requests.get(i["url"], stream=True)
                    response.raise_for_status()  # Raise an exception for HTTP errors
                    
                    # Write the content to the temporary file
                    for chunk in response.iter_content(chunk_size=8192):
                        temp_file.write(chunk)
                        
                    # Close the file
                    temp_file.close()
                    
                except Exception as e:
                    # Clean up the file if there's an error
                    temp_file.close()
                    os.unlink(temp_file.name)
                    return {"succeed": False, "error": str(e)}
                
                break
        else:
            return {"succeed": False, "error": "Generation failed. Please first make sure that all jobs of the task are done and then try again later."}

        try:
            obj = self._clean_imported_glb(
                filepath=temp_file.name,
                mesh_name=name
            )
            obj["initialname"] = name
           
            result = {
                "name": obj.name,
                "type": obj.type,
                "location": [obj.location.x, obj.location.y, obj.location.z],
                "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z],
                "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            }

            if obj.type == "MESH":
                bounding_box = self._get_aabb(obj)
                result["world_bounding_box"] = bounding_box
            
            return {
                "succeed": True, **result
            }
        except Exception as e:
            return {"succeed": False, "error": str(e)}
    
    def import_generated_asset_fal_ai(self, request_id: str, name: str):
        """Fetch the generated asset, import into blender"""
        response = requests.get(
            f"https://queue.fal.run/fal-ai/hyper3d/requests/{request_id}",
            headers={
                "Authorization": f"Key {bpy.context.scene.blendermcp_hyper3d_api_key}",
            }
        )
        data_ = response.json()
        temp_file = None
        
        temp_file = tempfile.NamedTemporaryFile(
            delete=False,
            prefix=request_id,
            suffix=".glb",
        )

        try:
            # Download the content
            response = requests.get(data_["model_mesh"]["url"], stream=True)
            response.raise_for_status()  # Raise an exception for HTTP errors
            
            # Write the content to the temporary file
            for chunk in response.iter_content(chunk_size=8192):
                temp_file.write(chunk)
                
            # Close the file
            temp_file.close()
            
        except Exception as e:
            # Clean up the file if there's an error
            temp_file.close()
            os.unlink(temp_file.name)
            return {"succeed": False, "error": str(e)}

        try:
            obj = self._clean_imported_glb(
                filepath=temp_file.name,
                mesh_name=name
            )
            obj["initialname"] = name
            
            result = {
                "name": obj.name,
                "type": obj.type,
                "location": [obj.location.x, obj.location.y, obj.location.z],
                "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z],
                "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            }

            if obj.type == "MESH":
                bounding_box = self._get_aabb(obj)
                result["world_bounding_box"] = bounding_box
            
            return {
                "succeed": True, **result
            }
        except Exception as e:
            return {"succeed": False, "error": str(e)}
    #endregion

# Blender UI Panel
class BLENDERMCP_PT_Panel(bpy.types.Panel):
    bl_label = "Blender MCP"
    bl_idname = "BLENDERMCP_PT_Panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'BlenderMCP'
    
    def draw(self, context):
        layout = self.layout
        scene = context.scene
        
        layout.prop(scene, "blendermcp_port")
        layout.prop(scene, "blendermcp_use_polyhaven", text="Use assets from Poly Haven")

        layout.prop(scene, "blendermcp_use_hyper3d", text="Use Hyper3D Rodin 3D model generation")
        if scene.blendermcp_use_hyper3d:
            layout.prop(scene, "blendermcp_hyper3d_mode", text="Rodin Mode")
            layout.prop(scene, "blendermcp_hyper3d_api_key", text="API Key")
            layout.operator("blendermcp.set_hyper3d_free_trial_api_key", text="Set Free Trial API Key")
        
        if not scene.blendermcp_server_running:
            layout.operator("blendermcp.start_server", text="Start MCP Server")
        else:
            layout.operator("blendermcp.stop_server", text="Stop MCP Server")
            layout.label(text=f"Running on port {scene.blendermcp_port}")

# Operator to set Hyper3D API Key
class BLENDERMCP_OT_SetFreeTrialHyper3DAPIKey(bpy.types.Operator):
    bl_idname = "blendermcp.set_hyper3d_free_trial_api_key"
    bl_label = "Set Free Trial API Key"
    
    def execute(self, context):
        context.scene.blendermcp_hyper3d_api_key = RODIN_FREE_TRIAL_KEY
        context.scene.blendermcp_hyper3d_mode = 'MAIN_SITE'
        self.report({'INFO'}, "API Key set successfully!")
        return {'FINISHED'}

# Operator to start the server
class BLENDERMCP_OT_StartServer(bpy.types.Operator):
    bl_idname = "blendermcp.start_server"
    bl_label = "Connect to Claude"
    bl_description = "Start the BlenderMCP server to connect with Claude"
    
    def execute(self, context):
        scene = context.scene
        
        # Create a new server instance
        if not hasattr(bpy.types, "blendermcp_server") or not bpy.types.blendermcp_server:
            bpy.types.blendermcp_server = BlenderMCPServer(port=scene.blendermcp_port)
        
        # Start the server
        bpy.types.blendermcp_server.start()
        scene.blendermcp_server_running = True
        
        return {'FINISHED'}

# Operator to stop the server
class BLENDERMCP_OT_StopServer(bpy.types.Operator):
    bl_idname = "blendermcp.stop_server"
    bl_label = "Stop the connection to Claude"
    bl_description = "Stop the connection to Claude"
    
    def execute(self, context):
        scene = context.scene
        
        # Stop the server if it exists
        if hasattr(bpy.types, "blendermcp_server") and bpy.types.blendermcp_server:
            bpy.types.blendermcp_server.stop()
            del bpy.types.blendermcp_server
        
        scene.blendermcp_server_running = False
        
        return {'FINISHED'}

# Registration functions
def register():
    bpy.types.Scene.blendermcp_port = IntProperty(
        name="Port",
        description="Port for the BlenderMCP server",
        default=9876,
        min=1024,
        max=65535
    )
    
    bpy.types.Scene.blendermcp_server_running = bpy.props.BoolProperty(
        name="Server Running",
        default=False
    )
    
    bpy.types.Scene.blendermcp_use_polyhaven = bpy.props.BoolProperty(
        name="Use Poly Haven",
        description="Enable Poly Haven asset integration",
        default=False
    )

    bpy.types.Scene.blendermcp_use_hyper3d = bpy.props.BoolProperty(
        name="Use Hyper3D Rodin",
        description="Enable Hyper3D Rodin generatino integration",
        default=False
    )

    bpy.types.Scene.blendermcp_hyper3d_mode = bpy.props.EnumProperty(
        name="Rodin Mode",
        description="Choose the platform used to call Rodin APIs",
        items=[
            ("MAIN_SITE", "hyper3d.ai", "hyper3d.ai"),
            ("FAL_AI", "fal.ai", "fal.ai"),
        ],
        default="MAIN_SITE"
    )

    bpy.types.Scene.blendermcp_hyper3d_api_key = bpy.props.StringProperty(
        name="Hyper3D API Key",
        subtype="PASSWORD",
        description="API Key provided by Hyper3D",
        default=""
    )
    
    bpy.utils.register_class(BLENDERMCP_PT_Panel)
    bpy.utils.register_class(BLENDERMCP_OT_SetFreeTrialHyper3DAPIKey)
    bpy.utils.register_class(BLENDERMCP_OT_StartServer)
    bpy.utils.register_class(BLENDERMCP_OT_StopServer)
    
    print("BlenderMCP addon registered")

def unregister():
    # Stop the server if it's running
    if hasattr(bpy.types, "blendermcp_server") and bpy.types.blendermcp_server:
        bpy.types.blendermcp_server.stop()
        del bpy.types.blendermcp_server
    
    bpy.utils.unregister_class(BLENDERMCP_PT_Panel)
    bpy.utils.unregister_class(BLENDERMCP_OT_SetFreeTrialHyper3DAPIKey)
    bpy.utils.unregister_class(BLENDERMCP_OT_StartServer)
    bpy.utils.unregister_class(BLENDERMCP_OT_StopServer)
    
    del bpy.types.Scene.blendermcp_port
    del bpy.types.Scene.blendermcp_server_running
    del bpy.types.Scene.blendermcp_use_polyhaven
    del bpy.types.Scene.blendermcp_use_hyper3d
    del bpy.types.Scene.blendermcp_hyper3d_mode
    del bpy.types.Scene.blendermcp_hyper3d_api_key

    print("BlenderMCP addon unregistered")

if __name__ == "__main__":
    register()
