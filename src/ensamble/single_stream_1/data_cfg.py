DATA_PATH = '/chenqs_ms/VX_race_data/'
BERT_PATH = '/home/tione/notebook/env/baseline/opensource_models/chinese-macbert-base'

DESC = {
    'tag_id':"int",
    'id': 'byte',
    'category_id': 'int',
    'title': 'byte',
    'asr_text': 'byte',
    'frame_feature': 'bytes'
}

DESC_NOTAG = {
    'id': 'byte',
    'title': 'byte',
    'asr_text': 'byte',
    'frame_feature': 'bytes'
}
