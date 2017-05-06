#!/usr/bin/env python

from setuptools import setup

setup(name='DogPush',
      version='0.3.4',
      description='DogPush: manage datadog alerts in local files.',
      author='Nadav S Samet',
      author_email='thesamet@gmail.com',
      license='Apache 2.0',
      url='https://github.com/trueaccord/DogPush',
      install_requires=[
          'PyYAML>=3.11',
          'datadog>=0.10.0',
          'pytz>=2015.7',
      ],
      packages=['dogpush'],
      scripts=['scripts/dogpush']
      )

