import hashlib,json,sys,tkinter as tk
from pathlib import Path
from tkinter import filedialog,messagebox,font
from PIL import ImageGrab
from aerokinetic.gui import Application
from aerokinetic.gui_style import install_theme
import aerokinetic
out=Path('/workspace/aerokinetic_ui_050/final_gui_release');out.mkdir(exist_ok=False)
root=tk.Tk();errors=[];expected_errors=[]
root.report_callback_exception=lambda *a:errors.append(str(a))
messagebox.showerror=lambda *a,**k:expected_errors.append(str(a))
messagebox.askyesnocancel=lambda *a,**k:False
app=Application(root);root.geometry('1280x720')
source=Path('/workspace/aerokinetic_ui_050/gui_v2/run_20261029_161718_809cec')
before={n:(source/n).read_bytes() for n in ('project.json','history.csv','residuals.csv','summary.json')}
filedialog.askdirectory=lambda **kw:str(source)
app.open_output();root.update()
assert app.document_readonly
filedialog.asksaveasfilename=lambda **kw:str(source/'project.json')
assert app.save_project() is False and len(expected_errors)==1
assert all((source/n).read_bytes()==raw for n,raw in before.items())
expected_errors.clear()
filedialog.asksaveasfilename=lambda **kw:str(out/'saved_copy.json')
assert app.save_project() is True and not app.document_readonly and not app.dirty
for section in ('geometry','mesh','calculation','results'):
 app.show_section(section)
 root.update();root.update()
 assert not errors
 box=(root.winfo_rootx(),root.winfo_rooty(),root.winfo_rootx()+root.winfo_width(),root.winfo_rooty()+root.winfo_height())
 ImageGrab.grab().crop(box).save(out/(section+'.png'))
app.variables['alpha_deg'].set('7');app.refresh_context()
assert app.dirty
app.show_section('results');assert 'α 5°' in app.point_note.get(),app.point_note.get()
app.show_section('geometry');assert 'α 7°' in app.point_note.get(),app.point_note.get()
for value in ('','nan','1000000'):
 app.variables['reynolds'].set(value);app.preview();root.update()
 assert app.entry_widgets['reynolds'].instate(['invalid'])
 assert app.variables['reynolds'].get()==value
app.variables['reynolds'].set('1e4');app.preview();root.update()
assert not app.form_error.get()
app.show_section('calculation')
for scale in (1.3359631821170281,2.0,2.6666666667):
 root.tk.call('tk','scaling',scale)
 install_theme(root)
 root.geometry('1280x720');root.update();root.update()
 assert all(b.winfo_rooty()+b.winfo_height() <= root.winfo_rooty()+root.winfo_height() for b in (app.start_button,app.resume_button,app.stop_button))
 assert all(b.winfo_rootx()+b.winfo_width() <= root.winfo_rootx()+root.winfo_width() for b in (app.start_button,app.resume_button,app.stop_button))
 box=(root.winfo_rootx(),root.winfo_rooty(),root.winfo_rootx()+root.winfo_width(),root.winfo_rooty()+root.winfo_height())
 ImageGrab.grab().crop(box).save(out/('scale_'+str(round(scale,2))+'.png'))
root.destroy()
assert not errors and not expected_errors
assert all((source/n).read_bytes()==raw for n,raw in before.items())
result={'status':'passed','version':aerokinetic.__version__,'installed':aerokinetic.__file__,'original_result_config_write_rejected':True,'save_copy':True,'separate_draft_result_context':True,'invalid_input_preserved_and_pointed':True,'sections_and_footer_1280x720':True,'linux_scaling':[1.3359631821170281,2.0,2.6666666667],'actual_windows_dpi':'not_run','result_files_unchanged':True,'errors':errors}
(out/'CHECK.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
