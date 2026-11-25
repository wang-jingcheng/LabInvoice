/* The editor and preview use local code; the server escapes raw HTML. */
function mountGuideEditor(container, initial, onChange) {
  container.innerHTML=`<div class="guide-controls"><label>编辑格式<select id="guideFormat"><option value="text">纯文本（兼容原说明）</option><option value="markdown">Markdown</option></select></label><div class="guide-view-tabs" role="group" aria-label="编辑视图"><button type="button" class="button ghost small" data-guide-view="edit">编辑</button><button type="button" class="button soft small" data-guide-view="split">分屏预览</button><button type="button" class="button ghost small" data-guide-view="preview">预览</button></div></div><div class="md-toolbar" role="toolbar" aria-label="Markdown 排版工具"><button type="button" data-md="heading" title="标题">标题</button><button type="button" data-md="bold" title="加粗">粗体</button><button type="button" data-md="italic" title="斜体">斜体</button><button type="button" data-md="list">列表</button><button type="button" data-md="quote">引用</button><button type="button" data-md="code">代码</button><button type="button" data-md="table">表格</button><button type="button" data-md="link">链接</button><label class="md-image-upload">上传图片<input id="guideImageInput" type="file" accept=".png,.jpg,.jpeg,.webp,.bmp,.gif,.tif,.tiff" hidden></label><label class="md-layout-option">宽度<select id="guideImageWidth"><option value="100">100%</option><option value="75">75%</option><option value="50">50%</option><option value="25">25%</option></select></label><label class="md-layout-option">对齐<select id="guideImageAlign"><option value="center">居中</option><option value="left">居左</option><option value="right">居右</option></select></label></div><div class="guide-edit-layout" data-view="split"><section class="guide-source-pane"><label class="sr-only" for="guideEditor">编辑网站使用说明</label><textarea id="guideEditor" class="guide-editor" maxlength="40000" spellcheck="false"></textarea></section><section class="guide-preview-pane" aria-label="使用说明预览"><div id="guidePreview" class="markdown-content"></div></section></div><p class="muted guide-editor-note">图片不超过 8 MB。图片上传后插入 Markdown，可用上方宽度和对齐控制排版；保存后发布。</p>`;
  const editor=$('#guideEditor',container),mode=$('#guideFormat',container),preview=$('#guidePreview',container);
  editor.value=initial.content;mode.value=initial.format;
  let timer,requestNumber=0, selection={start:0,end:0};
  const notify=()=>onChange({content:editor.value,format:mode.value});
  const refresh=async()=>{
    const request=++requestNumber;
    try{
      const result=await api('/api/admin/guide/preview',{method:'POST',body:{content:editor.value,format:mode.value}});
      if(request!==requestNumber||!container.isConnected)return;
      preview.className=mode.value==='markdown'?'markdown-content':'plain-content';
      preview.innerHTML=result.html;
    }catch(error){if(container.isConnected)preview.textContent=error.message;}
  };
  const changed=()=>{notify();clearTimeout(timer);timer=setTimeout(refresh,250);};
  const insert=(before,after='',fallback='文字')=>{
    const start=editor.selectionStart,end=editor.selectionEnd,text=editor.value.slice(start,end)||fallback;
    const next=before+text+after;
    if(editor.value.length-(end-start)+next.length>40000){toast('使用说明最多 40,000 字',true);return;}
    editor.setRangeText(next,start,end,'end');editor.focus();changed();
  };
  const setMarkdown=()=>{mode.value='markdown';notify();};
  editor.addEventListener('input',()=>{selection={start:editor.selectionStart,end:editor.selectionEnd};changed();});
  editor.addEventListener('select',()=>selection={start:editor.selectionStart,end:editor.selectionEnd});
  editor.addEventListener('keyup',()=>selection={start:editor.selectionStart,end:editor.selectionEnd});
  mode.addEventListener('change',changed);
  container.addEventListener('click',event=>{
    const view=event.target.closest('[data-guide-view]');
    if(view){$('.guide-edit-layout',container).dataset.view=view.dataset.guideView;$$('[data-guide-view]',container).forEach(b=>b.classList.toggle('soft',b===view));refresh();return;}
    const button=event.target.closest('[data-md]');if(!button)return;
    setMarkdown();
    const wrappers={heading:['\n## ','','标题'],bold:['**','**','加粗文字'],italic:['*','*','斜体文字'],list:['\n- ','','列表内容'],quote:['\n> ','','引用内容'],code:['\n```\n','\n```\n','代码内容'],link:['[','](https://example.com)','链接文字'],table:['\n| 报销项 | 材料要求 |\n| --- | --- |\n| ',' | 发票与附件 |\n','示例']};
    insert(...wrappers[button.dataset.md]);
  });
  editor.addEventListener('keydown',event=>{
    if((event.ctrlKey||event.metaKey)&&['b','i'].includes(event.key.toLowerCase())){event.preventDefault();setMarkdown();event.key.toLowerCase()==='b'?insert('**','**'):insert('*','*');}
  });
  const picker=$('#guideImageInput',container);
  picker.addEventListener('click',()=>selection={start:editor.selectionStart,end:editor.selectionEnd});
  picker.addEventListener('change',async()=>{
    const file=picker.files[0];if(!file)return;
    if(file.size>8*1048576){toast('说明图片不得超过 8 MB',true);picker.value='';return;}
    const width=$('#guideImageWidth',container).value,align=$('#guideImageAlign',container).value;
    const data=new FormData();data.append('image',file);picker.disabled=true;
    try {
      const result=await api('/api/admin/guide/images',{method:'POST',body:data});
      if(!container.isConnected)return;
      setMarkdown();editor.focus();editor.setSelectionRange(selection.start,selection.end);
      const alt=file.name.replace(/[\[\]\\\r\n]/g,' ').replace(/\.[^.]+$/,'');
      insert(`\n![${alt}](${result.url} "width=${width};align=${align}")\n`,'','');
      toast('图片已插入，请保存使用说明后发布');
    }catch(error){toast(error.message,true);}finally{picker.disabled=false;picker.value='';}
  });
  refresh();
}
