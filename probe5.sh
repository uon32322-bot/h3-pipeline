T=/root/miniconda3/lib/python3.12/site-packages/comfyui_workflow_templates_json/templates
for f in video_minimax_h3_i2v.json video_minimax_h3_multiframe_reference.json; do
  echo "########## FILE: $f ##########"
  echo "SIZE: $(stat -c %s $T/$f 2>/dev/null)"
  cat $T/$f 2>/dev/null
  echo
done
