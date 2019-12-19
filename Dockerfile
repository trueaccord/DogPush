FROM python:3.5

ADD requirements.txt /

RUN pip3 install -r /requirements.txt

ADD . /

ENTRYPOINT ["/dogpush/dogpush.py"]

