"""Extract the unambiguous Re=30000 triangle series, not author-supplied tables."""
import argparse
import csv
import hashlib
import json
from pathlib import Path


SPECS = {
    'cd': {'page_index':3,'figure':6,'printed_page':442,
           'frame':[339.4993591308594,414.22613525390625,539.633544921875,533.421875],
           'x_range':[-25.,25.],'y_range':[0.,.4], 'legend':[340.,480.,370.,525.],
           'coefficient_half_marker_bound':.0031},
    'cm_c4': {'page_index':5,'figure':11,'printed_page':444,
           'frame':[84.09861755371094,70.09184265136719,281.97540283203125,189.4114990234375],
           'x_range':[-25.,25.],'y_range':[-.1,.1], 'legend':[245.,75.,280.,112.],
           'coefficient_half_marker_bound':.0016}}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    import fitz
    doc=fitz.open(args.pdf)
    assert hashlib.sha256(args.pdf.read_bytes()).hexdigest()=='552c25268b8892c26f0cec7f45bc177f99b4109f926d3c60fc4a771c3a4924ee'
    provenance={'source_doi':'10.2322/jjsass.55.439',
        'source_url':'https://www.jstage.jst.go.jp/article/jjsass/55/644/55_644_439/_article/-char/en',
        'authors':'Tomohisa Ohtake, Yusuke Nakae, Tatsuo Motohashi',
        'year':2007,'access_date':'2026-11-06',
        'copyright':'The Japan Society for Aeronautical and Space Sciences, 2007; source rights retained',
        'pdf_sha256':hashlib.sha256(args.pdf.read_bytes()).hexdigest(),
        'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'digitizer':'PyMuPDF '+fitz.VersionBind,
        'reynolds':30000,'data_type':'Digitized published graph, not original author numeric data',
        'method':'Vector triangular-marker bounding-box centres, affine calibration from exact rectangular plot frame',
        'uncertainty_note':'Conservative half-marker bounds, not confidence intervals or wind-tunnel measurement uncertainty',
        'series':{}}
    for key,spec in SPECS.items():
        left,top,right,bottom=spec['frame'];xmin,xmax=spec['x_range'];ymin,ymax=spec['y_range']
        page=doc[spec['page_index']];rows=[];raw_count=legend_count=0
        overlay=fitz.open();overlay.insert_pdf(doc,from_page=spec['page_index'],to_page=spec['page_index'])
        for drawing in page.get_drawings():
            rect=drawing['rect'];cx,cy=(rect.x0+rect.x1)/2,(rect.y0+rect.y1)/2
            if not (drawing['type']=='fs' and len(drawing['items'])==3 and .8<rect.width<3 and .8<rect.height<3
                    and left<cx<right and top<cy<bottom):continue
            raw_count+=1
            l,t,r,b=spec['legend']
            if l<cx<r and t<cy<b:
                legend_count+=1;continue
            alpha=xmin+(cx-left)*(xmax-xmin)/(right-left)
            value=ymin+(bottom-cy)*(ymax-ymin)/(bottom-top)
            rows.append({'reynolds':30000,'alpha_deg':alpha,key:value,'digitization_alpha_bound_deg':.23,
                         'digitization_coefficient_bound':spec['coefficient_half_marker_bound'],
                         'pdf_marker_x':cx,'pdf_marker_y':cy})
            overlay[0].draw_circle(fitz.Point(cx,cy),1.6,color=(1,0,0),width=.35,overlay=True)
        assert raw_count==202 and legend_count==1 and len(rows)==201,(key,raw_count,legend_count,len(rows))
        rows.sort(key=lambda r:r['alpha_deg'])
        assert all(rows[i]['alpha_deg']<rows[i+1]['alpha_deg'] for i in range(len(rows)-1))
        assert rows[0]['alpha_deg']< -19 and rows[-1]['alpha_deg']>19
        output=args.output/f'ohtake2007_re30000_{key}.csv'
        with output.open('w',newline='',encoding='utf-8') as handle:
            writer=csv.DictWriter(handle,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
        overlay[0].get_pixmap(matrix=fitz.Matrix(3,3),clip=fitz.Rect(left-35,top-10,right+5,bottom+30)).save(args.output/(key+'_overlay.png'))
        provenance['series'][key]={**spec,'points':len(rows),'legend_markers_excluded':legend_count,
            'alpha_half_marker_bound_deg':.23,'csv_sha256':hashlib.sha256(output.read_bytes()).hexdigest()}
        print(key,'points',len(rows),'alpha range',rows[0]['alpha_deg'],rows[-1]['alpha_deg'])
    (args.output/'PROVENANCE.json').write_text(json.dumps(provenance,indent=2)+'\n')


if __name__=='__main__':main()
