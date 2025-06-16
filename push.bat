RMDIR /Q /S mcping_nonebot2.egg-info
RMDIR /Q /S dist
RMDIR /Q /S build
python setup.py sdist bdist_wheel
twine upload --repository-url https://upload.pypi.org/legacy/  dist/*
pause