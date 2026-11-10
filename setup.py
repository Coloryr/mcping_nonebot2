import setuptools
with open("README.md", "r", encoding='utf-8') as fh:
    long_description = fh.read()
setuptools.setup(
    name="mcping_nonebot2",
    version="2.0.0", 
    author="coloryr",  
    author_email="402067010@qq.com",
    description="mcping with nonebot2, with built-in /mc ping and MOTD renderer", 
    long_description=long_description,  
    long_description_content_type="text/markdown", 
    url="https://github.com/Coloryr/mcping_nonebot2",  
    packages=setuptools.find_packages(), 
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    install_requires=[
        "nonebot2>=2.5.0",
        "nonebot-adapter-qq>=1.7.0",
        "pillow>=10.0",
        "dnspython>=2.0",
    ],
    python_requires=">=3.9",
)