# Otter & e-Otter

## 🚀 Set Up for Otter
```bash
cd test-generation
conda create --name otter python=3.12
conda activate otter
conda install pip -y
python -m pip install datasets
python -m pip install litellm
python -m pip install gitpython 
python -m pip install Levenshtein
python -m pip install tree-sitter tree-sitter-python
python dataset_preparation.py
```

## 🚀 Set Up for e-Otter
```bash
cp TDD_Bench.json Test_execution
cd Test_execution
pip install -e .
cd ..
```
## LLM Configuration
This repository is primarily configured for Claude-Sonnet-4.5. To change LLM clients and model see <b>utility.py</b>. To run with the current cofiguration run the following command:
```bash
export CLAUDE_API="Your API Key"
```

## Generating Otter test
```bash
python otter.py --instance_id <INSERT INSTANCE _ID>
```
#### Example
```bash
python otter.py --instance_id django__django-10880
```

## Generating e-Otter test
<b>Note: Before running e-otter.py for specific instance id, you must run otter.py for that instance.</b>
```bash
python e-otter.py --instance_id <INSERT INSTANCE _ID>
```
#### Example
```bash
python e-otter.py --instance_id django__django-10880
```


## Evaluating the Test
Running otter.py and e-otter.py will generate and save the tests in otter_test.json & e_otter_test.json files. Copy the files to following benchmarks and follow the instruction.

[https://github.com/IBM/TDD-Bench-Verified](https://github.com/IBM/TDD-Bench-Verified)


## Performance with Claude-Sonnet-4.5 model

| Approach | # of fail-to-pass test | in % |
|----------|----------|----------|
| Otter   | 234   | 52.1%   |
| e-Otter   | 284   | 63.3%   |
