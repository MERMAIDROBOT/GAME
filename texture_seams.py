"""Periodic SR context and explicit seam validation; no inferred UV adjacency."""
import math


def policy(entry):
    axes=entry.get('repeat_axes', [])
    padding=entry.get('wrap_padding',16)
    if not isinstance(axes,list) or any(not isinstance(a,str) or a not in ('x','y') for a in axes) or len(axes)!=len(set(axes)):
        raise ValueError('repeat_axes must be a unique list of x/y axes')
    if type(padding) is not int or not 4<=padding<=32:
        raise ValueError('Wrap context must be 4..32 source pixels')
    return dict(axes=sorted(axes),padding=padding)


def pad(image,axes,padding):
    import numpy as np
    from PIL import Image
    rgba=np.asarray(image.convert('RGBA'))
    return Image.fromarray(np.pad(rgba,((padding,padding) if 'y' in axes else (0,0),
                                      (padding,padding) if 'x' in axes else (0,0),(0,0)),mode='wrap'))


def crop_box(image,axes,padding,scale=4):
    x=padding*scale if 'x' in axes else 0
    y=padding*scale if 'y' in axes else 0
    return (x,y,x+image.width*scale,y+image.height*scale)


def baseline(image,axes,padding):
    from PIL import Image
    context=pad(image,axes,padding)
    return context.convert('RGBa').resize((context.width*4,context.height*4),Image.Resampling.LANCZOS).convert('RGBA').crop(crop_box(image,axes,padding))


def finish(prediction,original,axes,padding):
    context=pad(original,axes,padding)
    if prediction.size!=(context.width*4,context.height*4):
        raise ValueError('Periodic prediction has incorrect dimensions')
    output=prediction.convert('RGBA').crop(crop_box(original,axes,padding))
    output.putalpha(baseline(original,axes,padding).getchannel('A'))
    return output


def tile_size(images):
    largest=max(max(image.size) for image in images)
    tile=max(32,2**math.ceil(math.log2(max(1,largest))))
    if tile>1024:
        raise ValueError('Periodic texture exceeds single model tile bound')
    return tile


def visible(image):
    import numpy as np
    rgba=np.asarray(image.convert('RGBA'),dtype=np.float64)
    rgba[:,:,:3]*=rgba[:,:,3:4]/255.0
    return rgba


def edge(image,side):
    a=visible(image)
    if side=='left': return a[:,0], a[:,1]-a[:,0] if image.width>1 else a[:,0]*0
    if side=='right': return a[:,-1], a[:,-1]-a[:,-2] if image.width>1 else a[:,-1]*0
    if side=='top': return a[0], a[1]-a[0] if image.height>1 else a[0]*0
    if side=='bottom': return a[-1], a[-1]-a[-2] if image.height>1 else a[-1]*0
    raise ValueError('Invalid seam edge')


def boundary(a,side_a,b,side_b,reverse=False):
    import numpy as np
    aa,da=edge(a,side_a); bb,db=edge(b,side_b)
    if aa.shape!=bb.shape:
        raise ValueError('Declared seam edges have different lengths')
    if reverse: bb=bb[::-1]; db=db[::-1]
    step=float(np.abs(aa-bb).mean())
    interior=float((np.abs(da).mean()+np.abs(db).mean())/2)
    return dict(boundary_step=step,interior_step=interior,ratio=step/max(1.0,interior))


def check_pair(a,ea,b,eb,enhanced_a,enhanced_b,reverse=False):
    before=boundary(a,ea,b,eb,reverse); after=boundary(enhanced_a,ea,enhanced_b,eb,reverse)
    # Relative criterion retains high-contrast foam/animated textures; do not
    # exclude them using a fixed maximum boundary brightness difference.
    limit=max(1.5,before['ratio']*1.25)
    return dict(before=before,after=after,limit_ratio=limit,passed=after['ratio']<=limit,
                scope='Declared-edge continuity only; not proof of correct UV mapping, perceptual fidelity or in-game loading')
