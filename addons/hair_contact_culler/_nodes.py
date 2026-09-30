# SPDX-License-Identifier: GPL-3.0-or-later
"""Native CURVES trimmed at the first contact, preserving root-side samples."""
import bpy


def _populate_group(g, meta, prefix, materials, point_count, curve_count, item_count):
    g['hc_owned'] = True
    for name, kind, direction in [('Geometry','NodeSocketGeometry','INPUT'),
                                  ('Geometry','NodeSocketGeometry','OUTPUT'),
                                  ('Viewport bits','NodeSocketInt','INPUT'),
                                  ('Render bits','NodeSocketInt','INPUT'),
                                  ('Captured bits','NodeSocketInt','INPUT'),
                                  ('Relevant bits','NodeSocketInt','INPUT'),
                                  ('Preview mode','NodeSocketInt','INPUT'),
                                  ('Low percent','NodeSocketInt','INPUT'),
                                  ('Valid','NodeSocketBool','INPUT')]:
        s = g.interface.new_socket(name=name, in_out=direction, socket_type=kind)
        if kind == 'NodeSocketInt': s.min_value=0; s.max_value=2147483647
        if name=='Valid': s.default_value=True
    n,l=g.nodes,g.links
    def node(kind, **props):
        x=n.new(kind)
        for k,v in props.items(): setattr(x,k,v)
        return x
    def wire(a,b): l.new(a,b)
    def attr(name, typ='INT'):
        x=node('GeometryNodeInputNamedAttribute',data_type=typ)
        x.inputs['Name'].default_value=prefix+name
        return x
    def compare(a,b,op='EQUAL',typ='INT'):
        x=node('FunctionNodeCompare', data_type=typ, operation=op)
        ai=0; bi=1
        wire(a,x.inputs[ai])
        if hasattr(b,'node'): wire(b,x.inputs[bi])
        else: x.inputs[bi].default_value=b
        return x.outputs[0]
    def boolean(a,b,op='AND'):
        x=node('FunctionNodeBooleanMath',operation=op); wire(a,x.inputs[0]); wire(b,x.inputs[1]); return x.outputs[0]
    def switch(typ, cond, false, true):
        x=node('GeometryNodeSwitch',input_type=typ); wire(cond,x.inputs['Switch']); wire(false,x.inputs['False']); wire(true,x.inputs['True']); return x.outputs[0]
    def math(a,b,op='ADD',integer=False):
        x=node('FunctionNodeIntegerMath' if integer else 'ShaderNodeMath',operation=op)
        if hasattr(a,'node'): wire(a,x.inputs[0])
        else: x.inputs[0].default_value=a
        if hasattr(b,'node'): wire(b,x.inputs[1])
        else: x.inputs[1].default_value=b
        return x.outputs[0]
    gi=node('NodeGroupInput'); go=node('NodeGroupOutput'); geom=gi.outputs['Geometry']
    view=node('GeometryNodeIsViewport').outputs[0]
    active=switch('INT',view,gi.outputs['Render bits'],gi.outputs['Viewport bits'])
    span=attr('span'); reverse=attr('reverse','BOOLEAN'); start=attr('start'); base=attr('base_cut','FLOAT')
    rel=node('FunctionNodeBitMath',operation='AND'); wire(active,rel.inputs[0]); wire(gi.outputs['Relevant bits'],rel.inputs[1])
    captured=compare(rel.outputs[0],gi.outputs['Captured bits'])
    # Minimum root-distance across enabled items gives the first active contact.
    # The captured state uses a precompiled scalar branch instead of this union.
    dynamic=span.outputs[0]; required=[span,reverse,start,base]
    for i in range(item_count):
        item=attr(f'cut_{i:02d}','FLOAT'); required.append(item)
        mask=node('FunctionNodeBitMath',operation='AND'); wire(active,mask.inputs[0]); mask.inputs[1].default_value=1<<i
        enabled=compare(mask.outputs[0],0,'NOT_EQUAL')
        dynamic=math(dynamic,switch('FLOAT',enabled,span.outputs[0],item.outputs[0]),'MINIMUM')
    cut=switch('FLOAT',captured,dynamic,base.outputs[0])
    # Guard live index/size correspondence before touching geometry. Failure shows source.
    idx=node('GeometryNodeInputIndex').outputs[0]; pid=attr('pid')
    mismatch=compare(pid.outputs['Attribute'],idx,'NOT_EQUAL')
    cp=node('GeometryNodeCurveOfPoint'); wire(idx,cp.inputs['Point Index']); sid=attr('sid')
    mismatch=boolean(mismatch,compare(sid.outputs[0],cp.outputs['Curve Index'],'NOT_EQUAL'),'OR')
    stat=node('GeometryNodeAttributeStatistic',domain='POINT'); wire(geom,stat.inputs['Geometry']); wire(mismatch,stat.inputs['Attribute'])
    size=node('GeometryNodeAttributeDomainSize',component='CURVE'); wire(geom,size.inputs['Geometry'])
    exists=node('GeometryNodeAttributeStatistic',domain='POINT'); wire(geom,exists.inputs['Geometry'])
    presence=boolean(boolean(pid.outputs['Exists'],sid.outputs['Exists']),attr('bucket').outputs['Exists'])
    presence=boolean(presence,attr('reference_radius','FLOAT').outputs['Exists'])
    for attribute in required: presence=boolean(presence,attribute.outputs['Exists'])
    wire(presence,exists.inputs['Attribute'])
    ctype=node('GeometryNodeInputNamedAttribute',data_type='INT'); ctype.inputs['Name'].default_value='curve_type'
    types=node('GeometryNodeAttributeStatistic',domain='CURVE'); wire(geom,types.inputs['Geometry']); wire(compare(ctype.outputs[0],1,'NOT_EQUAL'),types.inputs['Attribute'])
    cyclic=node('GeometryNodeInputNamedAttribute',data_type='BOOLEAN'); cyclic.inputs['Name'].default_value='cyclic'
    cstat=node('GeometryNodeAttributeStatistic',domain='CURVE'); wire(geom,cstat.inputs['Geometry']); wire(cyclic.outputs[0],cstat.inputs['Attribute'])
    expected=attr('reference_radius','FLOAT'); live=node('GeometryNodeInputRadius').outputs[0]
    rcompare=node('FunctionNodeCompare',data_type='FLOAT',operation='NOT_EQUAL'); wire(live,rcompare.inputs[0]); wire(expected.outputs[0],rcompare.inputs[1]); rcompare.inputs['Epsilon'].default_value=1e-8
    rstat=node('GeometryNodeAttributeStatistic',domain='POINT'); wire(geom,rstat.inputs['Geometry']); wire(rcompare.outputs[0],rstat.inputs['Attribute'])
    valid=gi.outputs['Valid']
    for cond in [compare(exists.outputs['Min'],1.0,typ='FLOAT'),compare(size.outputs['Point Count'],point_count),compare(size.outputs['Spline Count'],curve_count),compare(stat.outputs['Sum'],0.0,typ='FLOAT'),compare(types.outputs['Sum'],0.0,typ='FLOAT'),compare(cstat.outputs['Sum'],0.0,typ='FLOAT'),compare(rstat.outputs['Sum'],0.0,typ='FLOAT')]: valid=boolean(valid,cond)
    # Convert the saved edge/rank coordinate into current arc length. Sampling
    # the original full geometry follows its deformation and avoids Low's index
    # remapping. A fitted length fraction alone would move the cut to another
    # edge when different bends stretch by different amounts.
    source_coordinate=switch('FLOAT',reverse.outputs[0],cut,math(span.outputs[0],cut,'SUBTRACT'))
    floor=node('FunctionNodeFloatToInt',rounding_mode='FLOOR'); wire(source_coordinate,floor.inputs[0])
    next_local=math(math(floor.outputs[0],1,integer=True),span.outputs[0],'MINIMUM',integer=True)
    j=math(start.outputs[0],floor.outputs[0],integer=True)
    k=math(start.outputs[0],next_local,integer=True)
    parameter=node('GeometryNodeSplineParameter')
    def sample(index):
        x=node('GeometryNodeSampleIndex',data_type='FLOAT',domain='POINT'); wire(geom,x.inputs['Geometry']); wire(parameter.outputs['Length'],x.inputs['Value']); wire(index,x.inputs['Index']); return x.outputs[0]
    a=sample(j); b=sample(k)
    fraction=math(source_coordinate,floor.outputs[0],'SUBTRACT')
    length=math(a,math(math(b,a,'SUBTRACT'),fraction,'MULTIPLY'))
    # Low is a whole-curve subset of the trimmed original strands.
    omit=compare(attr('bucket').outputs[0],gi.outputs['Low percent'],'GREATER_EQUAL')
    low=boolean(view,compare(gi.outputs['Preview mode'],1))
    off=boolean(view,compare(gi.outputs['Preview mode'],2))
    total=node('GeometryNodeSplineLength').outputs['Length']
    has_cut=compare(cut,span.outputs[0],'LESS_THAN',typ='FLOAT')
    kept_length=switch('FLOAT',reverse.outputs[0],length,math(total,length,'SUBTRACT'))
    empty=boolean(has_cut,compare(kept_length,0.0,'LESS_EQUAL',typ='FLOAT'))
    subset=node('GeometryNodeDeleteGeometry',domain='CURVE'); wire(geom,subset.inputs['Geometry']); wire(boolean(boolean(low,omit),empty,'OR'),subset.inputs['Selection'])
    trim=node('GeometryNodeTrimCurve',mode='LENGTH'); wire(subset.outputs[0],trim.inputs['Curve'])
    wire(has_cut,trim.inputs['Selection'])
    trim_start=next(s for s in trim.inputs if s.name=='Start' and not s.is_unavailable)
    trim_end=next(s for s in trim.inputs if s.name=='End' and not s.is_unavailable)
    zero=node('ShaderNodeValue').outputs[0]; zero.default_value=0.0
    wire(switch('FLOAT',reverse.outputs[0],zero,length),trim_start)
    wire(switch('FLOAT',reverse.outputs[0],length,total),trim_end)
    result=trim.outputs[0]
    # Native curves have one component material; its selection must be a constant.
    for i,material in enumerate(materials):
        if material:
            sm=node('GeometryNodeSetMaterial'); sm.inputs['Material'].default_value=material
            wire(result,sm.inputs['Geometry']); sm.inputs['Selection'].default_value=True; result=sm.outputs[0]
    safe=switch('GEOMETRY',valid,geom,result)
    # Ordinary geometry switches evaluate only the chosen input. Off can skip the groom.
    final=node('GeometryNodeSwitch',input_type='GEOMETRY'); wire(boolean(valid,off),final.inputs['Switch']); wire(safe,final.inputs['False'])
    wire(final.outputs[0],go.inputs[0])
    # Layout only affects the node editor.
    for i,x in enumerate(n): x.location=((i%9)*210,-(i//9)*170)
    return g


def build_group(meta, prefix, materials, point_count, curve_count, item_count=None):
    g=bpy.data.node_groups.new('Hair Contact Cull','GeometryNodeTree')
    try: return _populate_group(g,meta,prefix,materials,point_count,curve_count,len(meta.helix_hair_cull.items) if item_count is None else item_count)
    except Exception:
        bpy.data.node_groups.remove(g)
        raise


def set_input(modifier, name, value):
    for s in modifier.node_group.interface.items_tree:
        if s.item_type=='SOCKET' and s.in_out=='INPUT' and s.name==name:
            field=getattr(modifier.properties.inputs,s.identifier)
            if field.value!=value:
                field.value=value
                modifier.id_data.update_tag()
            return
    raise KeyError(name)
