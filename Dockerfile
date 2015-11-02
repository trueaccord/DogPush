FROM python:2.7

ADD requirements.txt /

RUN pip install -r /requirements.txt

ADD . /

ENTRYPOINT ["/dogpush/dogpush.py"]

