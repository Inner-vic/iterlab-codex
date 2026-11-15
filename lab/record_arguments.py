"""Transport-only optional placeholders from pinned native tool schemas.

Never traverses content/inner JSON. Required fields and real values stay exact;
raw events are saved before this normalization. This grants no authority.
"""

def normalize(arguments,schema):
    optional=set(schema.get('properties',{}))-set(schema.get('required',[]))
    ignored=[key for key,value in arguments.items() if key in optional and
        (value is None or type(value) is str and value=='null')]
    return {key:value for key,value in arguments.items() if key not in ignored},ignored
