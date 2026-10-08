# SPDX-License-Identifier: GPL-3.0-or-later
"""Compiled root-to-first-contact hair trims. No scene Text or private assets."""
bl_info = {'name':'Hair Contact Culler', 'author':'Helices3D', 'version':(0, 4, 1),
           'blender':(5,2,2), 'location':'View3D > Sidebar > Helix Tools',
           'description':'Keep hair from its root to clothing contact; remove the remaining tip',
           'category':'Object'}

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from array import array
import hashlib, json, math, time, uuid
from . import _nodes
from ._ui import HelixPanel, section, setup_layout, wrapped_label
from .contacts import MeshContacts
from ._updates import create_updater

_UPDATER = create_updater(__package__, __file__)
_UPDATER_REGISTERED = False

MAX_ITEMS=31
CACHE_SCHEMA=2
_dirty=True
_busy=False
_registered=False


class InputError(ValueError): pass


def bundles():
    return [o for o in bpy.data.objects if o.helix_hair_cull.is_bundle]


def find_bundle(source):
    if source is None: return None
    return next((m for m in bundles() if m.helix_hair_cull.source==source),None)


def resolve_source(context):
    """Allow selecting one hair and its garments in either order."""
    active=context.active_object
    if active and active.type=='CURVES': return active
    selected=[o for o in context.selected_objects if o.type=='CURVES']
    return selected[0] if len(selected)==1 else None


def build_readiness(context):
    if context.mode!='OBJECT': return False,'Switch to Object Mode to build the hair trim.'
    source=resolve_source(context)
    if not source:
        return False,'Select one native hair object and its mesh garments.'
    meta=find_bundle(source)
    if meta:
        items=meta.helix_hair_cull.items
        if not items: return False,'Add at least one garment before rebuilding.'
        if len(items)>MAX_ITEMS: return False,'Use at most 31 garments in one setup.'
        if any(not i.obj or i.obj.type!='MESH' or i.obj.name not in context.scene.objects for i in items):
            return False,'Replace missing or non-mesh garments before rebuilding.'
        return True,'Rebuild uses the garment list below.'
    if source not in context.selected_objects:
        return False,'Select the hair and the garments you want it to stop at.'
    others=[o for o in context.selected_objects if o!=source]
    if not others: return False,'Also select at least one clothing or armor mesh.'
    if any(o.type!='MESH' for o in others):
        return False,'Select only one hair object and mesh garments; deselect rigs, cameras and other objects.'
    if len(others)>MAX_ITEMS: return False,'Use at most 31 garments in one setup.'
    if source.library or source.data.library:
        return False,'Use an editable local copy of the hair.'
    return True,f'Ready: {source.name} and {len(others)} garment'+('s.' if len(others)!=1 else '.')


def owned_modifier(meta):
    s=meta.helix_hair_cull
    return next((m for m in s.source.modifiers if m.type=='NODES' and m.node_group==s.group),None) if s.source and s.group else None


def _values(attr):
    kind=attr.data_type
    if not len(attr.data): return b''
    prop='vector' if hasattr(attr.data[0],'vector') else 'color' if hasattr(attr.data[0],'color') else 'value'
    value=getattr(attr.data[0],prop)
    size=len(value) if hasattr(value,'__len__') and not isinstance(value,str) else 1
    code='i' if kind in {'INT','BOOLEAN','INT8','INT32_2D'} else 'f'
    try:
        v=array(code,[0])*(len(attr.data)*size); attr.data.foreach_get(prop,v); return v.tobytes()
    except (AttributeError,TypeError,RuntimeError):
        return repr([tuple(d.vector) if hasattr(d,'vector') else tuple(d.color) if hasattr(d,'color') else d.value if hasattr(d,'value') else str(d) for d in attr.data]).encode()


def _data_hash(data, prefix=''):
    h=hashlib.sha256()
    if hasattr(data,'curves'):
        h.update(repr([(c.first_point_index,c.points_length) for c in data.curves]).encode())
    if hasattr(data,'polygons'):
        h.update(repr([tuple(p.vertices) for p in data.polygons]).encode())
        h.update(repr([tuple(e.vertices) for e in data.edges]).encode())
        for uv in data.uv_layers:
            h.update(uv.name.encode()); v=array('f',[0.0])*(2*len(uv.data)); uv.data.foreach_get('uv',v); h.update(v.tobytes())
    for a in sorted(data.attributes,key=lambda a:a.name):
        if a.name.startswith(prefix) and prefix: continue
        if a.name.startswith('.selection'): continue
        h.update((a.name+a.domain+a.data_type).encode()); h.update(_values(a))
    return h.hexdigest()


def fingerprint(meta):
    s=meta.helix_hair_cull; src=s.source
    if not src or src.type!='CURVES': raise InputError('Hair source is missing or changed type')
    scenes=[scene for scene in bpy.data.scenes if src.name in scene.objects]
    if len(scenes)!=1 or len(scenes[0].view_layers)!=1: raise InputError('Use one scene with one view layer for each setup')
    if s.compiled_source and (src!=s.compiled_source or src.data!=s.compiled_data or src.data.surface!=s.compiled_surface or len(s.items)!=len(s.compiled_items) or any(a.obj!=b.obj for a,b in zip(s.items,s.compiled_items))):
        raise InputError('Source or scoped item pointers changed')
    if s.compiled_source and (src.data.materials[0] if len(src.data.materials) else None)!=s.compiled_material:
        raise InputError('Hair material pointer changed')
    result=[_data_hash(src.data),src.data.surface_uv_map,s.captured_bits,s.relevant_bits,
            [(m.name,m.type,m.show_viewport,m.show_render) for m in src.modifiers if m!=owned_modifier(meta)],
            len(src.data.materials),s.allowance,s.root_mode,s.compiled_schema]
    surf=src.data.surface
    if surf:
        if surf.type!='MESH': raise InputError('Attachment surface must be a mesh')
        result.append(_data_hash(surf.data))
    for item in s.items:
        if not item.obj or item.obj.type!='MESH': raise InputError('A scoped garment is missing or changed type')
        result.append(_data_hash(item.obj.data))
        result.append([(m.name,m.type,m.show_viewport,m.show_render) for m in item.obj.modifiers])
    return hashlib.sha256(repr(result).encode()).hexdigest()


def uniform_scale(matrix):
    axes=[matrix.to_3x3().col[i] for i in range(3)]; lengths=[a.length for a in axes]
    scale=sum(lengths)/3
    if scale<1e-10 or max(lengths)-min(lengths)>scale*1e-5 or any(abs(axes[i].dot(axes[j]))>scale*scale*1e-5 for i in range(3) for j in range(i)):
        raise InputError('Hair world transform must have uniform scale and no shear; use a prepared copy')
    return scale


def read_curves(obj):
    data=obj.data
    if obj.type!='CURVES': raise InputError('Use native hair CURVES, with sampled POLY strands')
    mi=data.attributes.get('material_index')
    if len(data.materials)>1 or (mi and any(d.value!=0 for d in mi.data)):
        raise InputError('Version 0.2 supports one material per hair object; split a prepared copy by material')
    types=data.attributes.get('curve_type'); cyclic=data.attributes.get('cyclic'); radius=data.attributes.get('radius')
    # Native hair default is Catmull Rom when no curve_type attribute exists.
    if not types or types.domain!='CURVE' or types.data_type!='INT8' or any(d.value!=1 for d in types.data):
        raise InputError('Only sampled POLY strands are supported; unsampled curved splines are refused')
    if cyclic and any(d.value for d in cyclic.data): raise InputError('Cyclic strands are unsupported')
    if not radius or radius.domain!='POINT' or radius.data_type!='FLOAT': raise InputError('Hair needs an explicit finite POINT radius attribute')
    curves=[(c.first_point_index,c.points_length) for c in data.curves]
    if not curves or any(n<2 for _,n in curves): raise InputError('Each strand needs at least two samples')
    pos=[p.vector.copy() for p in data.attributes['position'].data]; rad=[d.value for d in radius.data]
    if any(not math.isfinite(x) for p in pos for x in p) or any(not math.isfinite(r) or r<0 for r in rad): raise InputError('Positions and nonnegative radii must be finite')
    scale=uniform_scale(obj.matrix_world)
    return curves,[obj.matrix_world@p for p in pos],[r*scale for r in rad]


def write_attr(data,name,typ,domain,values):
    a=data.attributes.get(name)
    if a and (a.data_type!=typ or a.domain!=domain): raise InputError('Owned cache attribute type changed')
    if not a: a=data.attributes.new(name,typ,domain)
    a.data.foreach_set('value',values)


def root_directions(source,offsets,positions,radii,dg,mode):
    """Resolve strand roots without reordering or editing the authored groom."""
    counts={'surface':0,'taper':0,'fallback_first':0,'explicit_first':0,'explicit_last':0}
    if mode in {'FIRST','LAST'}:
        counts['explicit_first' if mode=='FIRST' else 'explicit_last']=len(offsets)
        return [mode=='LAST']*len(offsets),counts
    surface=source.data.surface; tree=None
    if surface:
        if surface.type!='MESH': raise InputError('Attachment surface must be a mesh')
        from mathutils.bvhtree import BVHTree
        evaluated=surface.evaluated_get(dg); mesh=evaluated.to_mesh()
        try:
            mesh.calc_loop_triangles()
            vertices=[evaluated.matrix_world@v.co for v in mesh.vertices]
            if any(not math.isfinite(v) for p in vertices for v in p): raise InputError('Attachment positions must be finite')
            triangles=[tuple(t.vertices) for t in mesh.loop_triangles]
            if triangles: tree=BVHTree.FromPolygons(vertices,triangles,all_triangles=True)
        finally: evaluated.to_mesh_clear()
    reverse=[]
    for start,length in offsets:
        end=start+length-1; resolved=None
        if tree:
            first=tree.find_nearest(positions[start]); last=tree.find_nearest(positions[end])
            if first[0] is not None and last[0] is not None:
                a,b=first[3],last[3]
                tolerance=max(1e-8,1e-5*max(a,b,radii[start],radii[end]))
                if abs(a-b)>tolerance: resolved=b<a; counts['surface']+=1
        if resolved is None:
            a,b=radii[start],radii[end]
            if abs(a-b)>max(1e-12,1e-5*max(a,b)):
                resolved=b>a; counts['taper']+=1
            else: resolved=False; counts['fallback_first']+=1
        reverse.append(resolved)
    return reverse,counts


def _render_visible(obj,scene,view_layer):
    if obj.hide_render or obj.name not in scene.objects: return False
    def layer_find(lc):
        if lc.exclude or lc.collection.hide_render: return False
        if obj.name in lc.collection.objects: return True
        return any(layer_find(child) for child in lc.children)
    return layer_find(view_layer.layer_collection)


def active_bits(meta,render=False):
    scenes=[scene for scene in bpy.data.scenes if meta.helix_hair_cull.source and meta.helix_hair_cull.source.name in scene.objects]
    if len(scenes)!=1 or len(scenes[0].view_layers)!=1: return 0
    scene=scenes[0]; layer=scene.view_layers[0]
    out=0
    for i,item in enumerate(meta.helix_hair_cull.items):
        obj=item.obj
        if obj and (item.render if render else item.viewport):
            visible=_render_visible(obj,scene,layer) if render else obj.visible_get(view_layer=layer)
            if visible: out|=1<<i
    return out


def sync(meta,deep=False):
    s=meta.helix_hair_cull; mod=owned_modifier(meta)
    if not mod: s.valid=False; s.status='Build required'; return
    if s.compiled_schema!=CACHE_SCHEMA:
        s.valid=False; s.status='Rebuild required: upgrade to root-to-tip trimming'
    try: uniform_scale(s.source.matrix_world)
    except InputError as e: s.valid=False; s.status='Stale: '+str(e)+'; rebuild'
    if deep:
        try:
            if fingerprint(meta)!=s.signature: raise InputError('Source, UV, topology, radius, material, scope or tolerance changed')
            uniform_scale(s.source.matrix_world)
            if s.source.modifiers[-1]!=mod: raise InputError('Culler must remain the last modifier')
        except (InputError,ReferenceError,AttributeError) as e:
            s.valid=False; s.status='Stale: '+str(e)+'; rebuild'
    # Fail open to the intact source, rather than hiding from stale data.
    _nodes.set_input(mod,'Valid',s.valid)
    # Original comparison is viewport-only; keep the independent render mask.
    _nodes.set_input(mod,'Viewport bits',0 if s.preview_original else active_bits(meta))
    _nodes.set_input(mod,'Render bits',active_bits(meta,True))
    _nodes.set_input(mod,'Captured bits',s.captured_bits)
    _nodes.set_input(mod,'Relevant bits',s.relevant_bits)
    # Legacy graphs put Off outside their Valid fallback. Keep saved mode, but
    # send Full while invalid so an old setup also exposes the intact source.
    _nodes.set_input(mod,'Preview mode',{'FULL':0,'LOW':1,'OFF':2}[s.mode] if s.valid and not s.preview_original else 0)
    _nodes.set_input(mod,'Low percent',s.low_percent)


def create_bundle(source,items,scene=None):
    if not source: raise InputError('Select native POLY hair last so it is active')
    if find_bundle(source): raise InputError('Source already has a setup')
    if source.type!='CURVES' or source.library or source.data.library or sum(o.data==source.data for o in bpy.data.objects if o.type=='CURVES')!=1: raise InputError('Use one editable native CURVES object with single-user data')
    items=list(dict.fromkeys(items))
    if not items or len(items)>MAX_ITEMS or any(o.type!='MESH' for o in items): raise InputError('Select 1 to 31 explicit mesh garments')
    meta=bpy.data.objects.new('Hair Culling Settings',None); (scene or bpy.context.scene).collection.objects.link(meta)
    meta.hide_viewport=True; meta.hide_render=True
    s=meta.helix_hair_cull; s.is_bundle=True; s.source=source; s.prefix='hcx_'+uuid.uuid4().hex[:10]+'_'
    for obj in items: s.items.add().obj=obj
    return meta


def compile_bundle(meta):
    global _busy
    if _busy: raise InputError('A rebuild is already running')
    started=time.perf_counter(); s=meta.helix_hair_cull; src=s.source; old=owned_modifier(meta)
    created=[]; old_flags=None; new_group=None; original_mod=old; original_group=s.group
    try: backup={a.name:(a.data_type,a.domain,[d.value for d in a.data]) for a in src.data.attributes if a.name.startswith(s.prefix)} if src else {}
    except AttributeError as e: raise InputError('Cache attribute type changed; remove this setup before rebuilding') from e
    saved={name:getattr(s,name) for name in ('signature','captured_bits','relevant_bits','valid','status','stats','root_summary','compiled_schema','compiled_source','compiled_data','compiled_surface','compiled_material')}
    saved_items=[i.obj for i in s.compiled_items]
    _busy=True
    try:
        if not src or src.library or src.data.library or sum(o.data==src.data for o in bpy.data.objects if o.type=='CURVES')!=1: raise InputError('Source must be editable and have single-user data')
        if not 1<=len(s.items)<=MAX_ITEMS or len({i.obj for i in s.items})!=len(s.items): raise InputError('Scope needs 1 to 31 distinct meshes')
        raw_offsets,_,_=read_curves(src)
        np=len(src.data.points); nc=len(raw_offsets)
        if len([scene for scene in bpy.data.scenes if src.name in scene.objects])!=1 or len(bpy.context.scene.view_layers)!=1:
            raise InputError('Version 0.2 supports a source in one scene with one view layer')
        if old: old_flags=(old.show_viewport,old.show_render); old.show_viewport=False; old.show_render=False
        # Stable IDs are seeded before prefix evaluation and rolled back on failure.
        sid=[0]*np
        for ci,(start,count) in enumerate(raw_offsets):
            for j in range(start,start+count): sid[j]=ci
        for name,vals in [('pid',list(range(np))),('sid',sid)]:
            full=s.prefix+name
            a=src.data.attributes.get(full)
            if a:
                if a.domain!='POINT' or a.data_type!='INT' or [d.value for d in a.data]!=vals: raise InputError('Stable source ID mapping changed; remove setup before reauthoring topology')
            else: write_attr(src.data,full,'INT','POINT',vals); created.append(full)
        src.data.update_tag(); bpy.context.view_layer.update()
        dg=bpy.context.evaluated_depsgraph_get(); evaluated=src.evaluated_get(dg)
        offsets,positions,radii=read_curves(evaluated)
        if offsets!=raw_offsets or len(positions)!=np: raise InputError('Prefix modifiers changed sample topology')
        for name,vals in [('pid',list(range(np))),('sid',sid)]:
            a=evaluated.data.attributes.get(s.prefix+name)
            if not a or [d.value for d in a.data]!=vals: raise InputError('Prefix modifiers lost or reordered stable source IDs')
        reverse,root_counts=root_directions(src,offsets,positions,radii,dg,s.root_mode)
        spans=[length-1 for _,length in offsets]
        cuts=[]; per_item=[]
        for i,item in enumerate(s.items):
            obj=item.obj
            if not obj or obj.type!='MESH' or obj.name not in bpy.context.scene.objects: raise InputError('Scoped item must be a mesh in this scene')
            eo=obj.evaluated_get(dg); mesh=eo.to_mesh()
            try:
                mesh.calc_loop_triangles(); triangles=[tuple(eo.matrix_world@mesh.vertices[j].co for j in t.vertices) for t in mesh.loop_triangles]
                if any(not math.isfinite(v) for tri in triangles for p in tri for v in p): raise InputError('Garment positions must be finite')
                contact=MeshContacts(triangles); count=0; item_cuts=list(spans)
                for ci,(start,length) in enumerate(offsets):
                    for rank in range(length-1):
                        a=start+length-1-rank if reverse[ci] else start+rank
                        b=a-1 if reverse[ci] else a+1
                        t=contact.first_contact(positions[a],positions[b],radii[a],radii[b],s.allowance)
                        if t is not None:
                            item_cuts[ci]=rank+t; count+=1; break
                cuts.append(item_cuts)
                per_item.append(count)
            finally: eo.to_mesh_clear()
        relevant=sum(1<<i for i,count in enumerate(per_item) if count)
        captured=active_bits(meta)&relevant
        base=[min([cut[ci] for i,cut in enumerate(cuts) if captured&(1<<i)]+[span]) for ci,span in enumerate(spans)]
        # Material identity is retained per original strand, then per fragment.
        mi=src.data.attributes.get('material_index')
        mats=[0]*np
        if mi:
            for ci,(start,length) in enumerate(offsets):
                for j in range(start,start+length): mats[j]=mi.data[ci].value
        new_group=_nodes.build_group(meta,s.prefix,list(src.data.materials),np,nc,len(cuts))
        buckets=[((v*1103515245+12345)&0x7fffffff)%100 for v in sid]
        evaluated_scale=uniform_scale(evaluated.matrix_world)
        reference_radius=[r/evaluated_scale for r in radii]
        for name,typ,values in [('mat','INT',mats),('bucket','INT',buckets),('reference_radius','FLOAT',reference_radius)]: write_attr(src.data,s.prefix+name,typ,'POINT',values)
        for name,typ,values in [('reverse','BOOLEAN',reverse),('start','INT',[start for start,_ in offsets]),('span','INT',spans),('base_cut','FLOAT',base)]: write_attr(src.data,s.prefix+name,typ,'CURVE',values)
        for i,values in enumerate(cuts): write_attr(src.data,s.prefix+f'cut_{i:02d}','FLOAT','CURVE',values)
        if old:
            old_group=old.node_group; old.node_group=new_group
        else:
            old=src.modifiers.new('Hair Contact Culler','NODES'); old.node_group=new_group; old_group=None
        s.group=new_group; s.relevant_bits=relevant; s.captured_bits=captured
        s.compiled_source=src; s.compiled_data=src.data; s.compiled_surface=src.data.surface
        s.compiled_material=src.data.materials[0] if len(src.data.materials) else None
        s.compiled_items.clear()
        for item in s.items: s.compiled_items.add().obj=item.obj
        s.compiled_schema=CACHE_SCHEMA
        s.root_summary=', '.join(f'{value} {key.replace("_"," ")}' for key,value in root_counts.items() if value)
        s.signature=fingerprint(meta); s.valid=True
        trimmed=sum(cut<span for cut,span in zip(base,spans))
        s.status=f'{trimmed} / {nc} strands trimmed; {time.perf_counter()-started:.3f}s build'
        s.stats=json.dumps({'per_item_strands':per_item,'points':np,'strands':nc,'trimmed_strands':trimmed,'relevant_bits':relevant,'root_counts':root_counts,'build_seconds':time.perf_counter()-started})
        sync(meta)
        if old_group and old_group.users==0: bpy.data.node_groups.remove(old_group)
        return json.loads(s.stats)
    except Exception:
        if src:
            for a in list(src.data.attributes):
                if a.name.startswith(s.prefix): src.data.attributes.remove(a)
            for name,(typ,domain,vals) in backup.items(): write_attr(src.data,name,typ,domain,vals)
        if original_mod: original_mod.node_group=original_group
        elif old and src: src.modifiers.remove(old); old=None
        s.group=original_group
        for name,value in saved.items(): setattr(s,name,value)
        s.compiled_items.clear()
        for obj in saved_items: s.compiled_items.add().obj=obj
        if new_group and new_group.users==0: bpy.data.node_groups.remove(new_group)
        raise
    finally:
        if old and old_flags: old.show_viewport,old.show_render=old_flags
        _busy=False; bpy.context.view_layer.update()


def remove_bundle(meta):
    s=meta.helix_hair_cull; src=s.source; group=s.group; mod=owned_modifier(meta)
    if mod: src.modifiers.remove(mod)
    if src:
        for a in list(src.data.attributes):
            if a.name.startswith(s.prefix): src.data.attributes.remove(a)
    bpy.data.objects.remove(meta,do_unlink=True)
    if group and group.users==0: bpy.data.node_groups.remove(group)


def _changed(self,context):
    global _dirty
    _dirty=True
    # Controls update immediately; geometry/cache validation stays in explicit build or watcher.
    if isinstance(self.id_data,bpy.types.Object) and self.id_data.helix_hair_cull.is_bundle and not _busy:
        sync(self.id_data)


def _root_changed(self,context):
    if self.is_bundle and not _busy:
        self.valid=False; self.status='Root direction changed; rebuild required'
    _changed(self,context)


def _scope_changed(self,context):
    owner=self.id_data
    if isinstance(owner,bpy.types.Object) and owner.helix_hair_cull.is_bundle and not _busy:
        settings=owner.helix_hair_cull
        settings.valid=False; settings.status='Garment list changed; rebuild required'
    _changed(self,context)


def _clearance_changed(self,context):
    if self.is_bundle and not _busy:
        self.valid=False; self.status='Extra clearance changed; rebuild required'
    _changed(self,context)


class HC_Item(bpy.types.PropertyGroup):
    obj: PointerProperty(name='Garment',description='Mesh that this hair stops at; changing it requires Rebuild Hair Trim',type=bpy.types.Object,update=_scope_changed)
    viewport: BoolProperty(name='Trim in view',description='Use this garment to trim hair in the viewport; does not hide the garment. Hidden garments do not trim there',default=True,update=_changed)
    render: BoolProperty(name='Trim in render',description='Use this garment to trim rendered hair; does not hide the garment. Garments hidden from rendering do not trim there',default=True,update=_changed)


class HC_Settings(bpy.types.PropertyGroup):
    is_bundle: BoolProperty(default=False)
    source: PointerProperty(type=bpy.types.Object)
    compiled_source: PointerProperty(type=bpy.types.Object)
    compiled_data: PointerProperty(type=bpy.types.Curves)
    compiled_surface: PointerProperty(type=bpy.types.Object)
    compiled_material: PointerProperty(type=bpy.types.Material)
    compiled_items: CollectionProperty(type=HC_Item)
    group: PointerProperty(type=bpy.types.NodeTree)
    prefix: StringProperty()
    signature: StringProperty()
    compiled_schema: IntProperty(default=0)
    root_mode: EnumProperty(name='Strand root',description='Which end to keep. Change this if trimming keeps the wrong side, then rebuild',items=[('AUTO','Auto','Find each root using the attached skin surface, then the thicker endpoint, then the first stored point'),('FIRST','First point','Keep the side toward the first stored point of every strand; rebuild after changing'),('LAST','Last point','Keep the side toward the last stored point of every strand; rebuild after changing')],default='AUTO',update=_root_changed)
    root_summary: StringProperty()
    items: CollectionProperty(type=HC_Item)
    mode: EnumProperty(name='Viewport detail',items=[('FULL','Full','Show all trimmed strands in the viewport'),('LOW','Low','Show fewer complete trimmed strands to simplify the viewport; rendering keeps all strands'),('OFF','Hide','Hide hair in the viewport only; rendering still uses the full trimmed hair')],default='FULL',update=_changed)
    preview_original: BoolProperty(name='Show Original',description='Compare the complete untrimmed hair in the viewport. Keeps the setup and detail mode; rendering still uses the trimmed result',default=False,update=_changed)
    low_percent: IntProperty(name='Strands shown %',description='Percentage of complete trimmed strands shown in Low; does not reduce render detail',default=20,min=1,max=100,update=_changed)
    allowance: FloatProperty(name='Extra clearance',description='Additional world-space distance around each strand. Increase slightly to stop hair sooner, then rebuild. Blender units',default=0.00002,min=0,max=1,precision=6,update=_clearance_changed)
    valid: BoolProperty(default=False)
    captured_bits: IntProperty(default=0,min=0)
    relevant_bits: IntProperty(default=0,min=0)
    status: StringProperty(default='Build required')
    stats: StringProperty()


class HC_OT_build(bpy.types.Operator):
    bl_idname='helix.hair_cull_build'; bl_label='Build / Rebuild Hair Trim'; bl_options={'REGISTER','UNDO'}
    bl_description='Keep each strand from its root to its first garment contact. New setup uses selected garments; Rebuild uses the existing list'
    @classmethod
    def poll(cls,context):
        ready,message=build_readiness(context)
        if not ready: cls.poll_message_set(message)
        return ready
    def execute(self,context):
        source=resolve_source(context); meta=find_bundle(source); fresh=False
        try:
            if not meta:
                meta=create_bundle(source,[o for o in context.selected_objects if o!=source]); fresh=True
            compile_bundle(meta); self.report({'INFO'},meta.helix_hair_cull.status); return {'FINISHED'}
        except (InputError,ValueError,RuntimeError) as e:
            if fresh and meta: remove_bundle(meta)
            self.report({'ERROR'},str(e)); return {'CANCELLED'}


class HC_OT_remove(bpy.types.Operator):
    bl_idname='helix.hair_cull_remove'; bl_label='Remove Setup'; bl_options={'REGISTER','UNDO'}
    bl_description='Restore the original groom and remove only this add-on\'s modifier, trim attributes and settings'
    def execute(self,context):
        meta=find_bundle(resolve_source(context))
        if not meta: return {'CANCELLED'}
        remove_bundle(meta); return {'FINISHED'}


class HC_OT_validate(bpy.types.Operator):
    bl_idname='helix.hair_cull_validate'; bl_label='Check Saved Inputs'
    bl_description='Check whether stored source data and setup still match. Does not recompute collisions; rebuild after a pose or fit change'
    def execute(self,context):
        meta=find_bundle(resolve_source(context))
        if not meta: return {'CANCELLED'}
        sync(meta,deep=True); self.report({'INFO' if meta.helix_hair_cull.valid else 'WARNING'},meta.helix_hair_cull.status); return {'FINISHED'}


class HC_OT_add_items(bpy.types.Operator):
    bl_idname='helix.hair_cull_add_items'; bl_label='Add Selected Garments'; bl_options={'REGISTER','UNDO'}
    bl_description='Select this hair and additional clothing meshes in either order. Append them to this list, then rebuild'
    def execute(self,context):
        source=resolve_source(context); meta=find_bundle(source)
        if not meta: self.report({'ERROR'},'Select hair with an existing setup.'); return {'CANCELLED'}
        s=meta.helix_hair_cull; selected=[o for o in context.selected_objects if o!=source]
        if not selected or any(o.type!='MESH' or o.name not in context.scene.objects for o in selected):
            self.report({'ERROR'},'Select this hair and the additional clothing meshes only.'); return {'CANCELLED'}
        additions=[o for o in selected if o not in {i.obj for i in s.items}]
        if not additions: self.report({'INFO'},'Selected garments are already in the list.'); return {'CANCELLED'}
        if len(s.items)+len(additions)>MAX_ITEMS:
            self.report({'ERROR'},'Use at most 31 garments in one setup.'); return {'CANCELLED'}
        for obj in additions: s.items.add().obj=obj
        self.report({'INFO'},'Garments added. Rebuild Hair Trim to update the result.'); return {'FINISHED'}


class HC_OT_remove_item(bpy.types.Operator):
    bl_idname='helix.hair_cull_remove_item'; bl_label='Remove Garment'; bl_options={'REGISTER','UNDO'}
    bl_description='Remove this garment from the trim list without deleting or hiding it; rebuild afterward'
    index: IntProperty(default=-1)
    def execute(self,context):
        meta=find_bundle(resolve_source(context))
        if not meta or not 0<=self.index<len(meta.helix_hair_cull.items): return {'CANCELLED'}
        meta.helix_hair_cull.items.remove(self.index); _scope_changed(meta.helix_hair_cull,context)
        self.report({'INFO'},'Garment removed from the list. Rebuild Hair Trim to update.'); return {'FINISHED'}


class HC_OT_help(bpy.types.Operator):
    bl_idname='helix.hair_cull_help'; bl_label='Quick Guide'
    bl_description='Show the setup, preview and rebuild workflow without leaving Blender'
    def invoke(self,context,event): return context.window_manager.invoke_popup(self,width=440)
    def execute(self,context): return {'FINISHED'}
    def draw(self,context):
        layout=self.layout
        for title,text in [
            ('1. Select','Select one native hair object and its clothing meshes. Selection order does not matter.'),
            ('2. Build','Click Build Hair Trim. Auto finds the strand roots and keeps hair up to its first clothing contact.'),
            ('3. Compare','Toggle Show Original to compare the complete groom. Turn it off to inspect the trimmed result.'),
            ('4. Adjust','If the wrong end remains, change Strand root to First point or Last point and rebuild.'),
            ('5. Update clothing','Add Selected Garments or remove a row, then rebuild. Rebuild uses the list, not your current selection.'),
            ('6. Preview and render','Full, Low and Hide affect the viewport. Rendering uses all trimmed strands. Garment switches control where each garment trims.'),
            ('7. Refit or restore','Rebuild after changing the pose or clothing fit. Maintenance > Remove Setup restores the original groom.')]:
            col=layout.box().column()
            col.label(text=title)
            wrapped_label(col,text,context,width=54)


class HC_PT_panel(HelixPanel,bpy.types.Panel):
    bl_idname='HC_PT_contact_culler'; bl_label='Hair Contact Culler'
    bl_order=50
    def draw(self,context):
        layout=setup_layout(self.layout)
        source=resolve_source(context); meta=find_bundle(source)
        ready,message=build_readiness(context)
        build=section(layout,'Hair Trim',icon='MOD_MASK',section_id='build_trim')
        if build is not None:
            if source: build.label(text=source.name,icon='CURVES')
            row=build.row(); row.scale_y=1.25; row.enabled=ready
            row.operator(HC_OT_build.bl_idname,
                         text='Rebuild Hair Trim' if meta else 'Build Hair Trim',
                         icon='FILE_REFRESH' if meta else 'MOD_MASK')
            if not ready:
                wrapped_label(build,message,context,icon='ERROR' if meta else 'INFO')
            elif meta:
                s=meta.helix_hair_cull
                if s.valid: build.label(text='Trim ready',icon='CHECKMARK')
                else: wrapped_label(build,s.status,context,icon='ERROR')
        if not meta:
            setup=section(layout,'Garments',icon='MESH_DATA',section_id='garments')
            if setup is not None:
                garments=[obj for obj in context.selected_objects if obj!=source]
                for obj in garments:
                    setup.label(text=obj.name,icon='MESH_DATA' if obj.type=='MESH' else 'ERROR')
                if not garments: setup.label(text='No garments selected',icon='INFO')
            layout.operator(HC_OT_help.bl_idname,icon='HELP')
            return
        s=meta.helix_hair_cull
        setup=section(layout,'Garments',icon='MESH_DATA',section_id='garments')
        if setup is not None:
            for i,item in enumerate(s.items):
                garment=setup.box(); row=garment.row(align=True)
                row.label(text=item.obj.name if item.obj else 'Missing garment',icon='MESH_DATA' if item.obj else 'ERROR')
                row.operator(HC_OT_remove_item.bl_idname,text='',icon='X').index=i
                row=garment.row(align=True); row.use_property_split=False
                row.prop(item,'viewport',text='Viewport',toggle=True)
                row.prop(item,'render',text='Render',toggle=True)
                if item.obj:
                    hidden=[]
                    if not item.obj.visible_get(view_layer=context.view_layer): hidden.append('viewport')
                    if not _render_visible(item.obj,context.scene,context.view_layer): hidden.append('render')
                    if hidden: wrapped_label(garment,'No trim in '+ ' / '.join(hidden),context,icon='HIDE_ON')
            if not s.items: setup.label(text='No garments in setup',icon='ERROR')
            setup.operator(HC_OT_add_items.bl_idname,icon='ADD')
            setup.prop(s,'root_mode',text='Strand Root')

        preview=section(layout,'Viewport Preview',icon='HIDE_OFF',section_id='preview')
        if preview is not None:
            row=preview.row(); row.enabled=s.valid; row.use_property_split=False
            row.prop(s,'preview_original',toggle=True)
            if not s.valid: preview.label(text='Original shown until rebuild',icon='INFO')
            detail=preview.column(); detail.enabled=s.valid and not s.preview_original
            detail.label(text='Viewport Detail')
            modes=detail.row(align=True); modes.use_property_split=False
            modes.prop(s,'mode',expand=True)
            if s.mode=='LOW': detail.prop(s,'low_percent',text='Strands (%)')

        advanced=section(layout,'Advanced',icon='PREFERENCES',section_id='advanced',default_closed=True)
        if advanced is not None:
            advanced.prop(s,'allowance',text='Extra Clearance')
            if s.root_summary:
                advanced.label(text='Last Root Detection',icon='CURVES')
                wrapped_label(advanced,s.root_summary,context)
        maintenance=section(layout,'Maintenance',icon='TOOL_SETTINGS',section_id='maintenance',default_closed=True)
        if maintenance is not None:
            wrapped_label(maintenance,s.status,context,icon='CHECKMARK' if s.valid else 'ERROR')
            maintenance.operator(HC_OT_validate.bl_idname,icon='CHECKMARK')
            maintenance.operator(HC_OT_remove.bl_idname,icon='LOOP_BACK')
        layout.operator(HC_OT_help.bl_idname,icon='HELP')


@persistent
def _depsgraph(scene,dg):
    global _dirty
    if not _busy and any(u.is_updated_geometry for u in dg.updates): _dirty=True


@persistent
def _restore(*args):
    global _dirty
    _dirty=True
    for meta in bundles():
        if meta.helix_hair_cull.source and meta.helix_hair_cull.source.name in bpy.context.scene.objects: sync(meta,deep=True)


def _tick():
    global _dirty
    if not _busy:
        deep=_dirty; _dirty=False
        for meta in bundles():
            if not meta.helix_hair_cull.source or meta.helix_hair_cull.source.name not in bpy.context.scene.objects: continue
            try: sync(meta,deep=deep)
            except (ReferenceError,RuntimeError,InputError): meta.helix_hair_cull.valid=False
    return 0.5


@persistent
def _render_pre(scene):
    for meta in bundles():
        if meta.helix_hair_cull.source and meta.helix_hair_cull.source.name in scene.objects: sync(meta,deep=True)


CLASSES=(HC_Item,HC_Settings,HC_OT_build,HC_OT_remove,HC_OT_validate,HC_OT_add_items,HC_OT_remove_item,HC_OT_help,HC_PT_panel)
HANDLERS=((bpy.app.handlers.depsgraph_update_post,_depsgraph),(bpy.app.handlers.load_post,_restore),(bpy.app.handlers.undo_post,_restore),(bpy.app.handlers.redo_post,_restore),(bpy.app.handlers.render_pre,_render_pre))


def register():
    global _registered, _UPDATER_REGISTERED
    if _registered: return
    if hasattr(bpy.types.Object,'helix_hair_cull'): raise RuntimeError('Hair culling settings already registered by another module')
    for cls in CLASSES:
        if issubclass(cls,bpy.types.Operator):
            namespace,name=cls.bl_idname.split('.')
            existing=bpy.types.Operator.bl_rna_get_subclass_py(namespace.upper()+'_OT_'+name)
        elif issubclass(cls,bpy.types.Panel): existing=bpy.types.Panel.bl_rna_get_subclass_py(cls.bl_idname)
        else: existing=bpy.types.PropertyGroup.bl_rna_get_subclass_py(cls.__name__)
        if existing: raise RuntimeError('Hair culling class or operator ID already registered: '+cls.__name__)
    done=[]
    try:
        _UPDATER.register()
        _UPDATER_REGISTERED=True
        for cls in CLASSES: bpy.utils.register_class(cls); done.append(cls)
        bpy.types.Object.helix_hair_cull=PointerProperty(type=HC_Settings)
        for handler,fn in HANDLERS:
            if fn not in handler: handler.append(fn)
        if not bpy.app.timers.is_registered(_tick): bpy.app.timers.register(_tick,first_interval=0.5,persistent=True)
        # addon_utils uses restricted data/context during registration. Timer/load handlers restore later.
        _registered=True
    except Exception:
        try:
            if hasattr(bpy.types.Object,'helix_hair_cull'): del bpy.types.Object.helix_hair_cull
            for handler,fn in HANDLERS:
                if fn in handler: handler.remove(fn)
            if bpy.app.timers.is_registered(_tick): bpy.app.timers.unregister(_tick)
            for cls in reversed(done): bpy.utils.unregister_class(cls)
        finally:
            if _UPDATER_REGISTERED:
                _UPDATER.unregister()
                _UPDATER_REGISTERED=False
        raise


def unregister():
    global _registered, _UPDATER_REGISTERED
    if _UPDATER_REGISTERED:
        _UPDATER.unregister()
        _UPDATER_REGISTERED=False
    if not _registered: return
    if bpy.app.timers.is_registered(_tick): bpy.app.timers.unregister(_tick)
    for handler,fn in HANDLERS:
        if fn in handler: handler.remove(fn)
    # Static saved nodes remain useful when disabled; restore original source while unregistering.
    for meta in bundles():
        mod=owned_modifier(meta)
        if mod: _nodes.set_input(mod,'Valid',False)
    del bpy.types.Object.helix_hair_cull
    for cls in reversed(CLASSES): bpy.utils.unregister_class(cls)
    _registered=False
