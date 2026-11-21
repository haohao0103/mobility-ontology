# 如何把这个基准发布成独立仓库

本目录**完全自包含**（只依赖 Python 标准库、不依赖本仓任何代码），
因此可以直接复制成独立仓库对外发布。

## 三步

```bash
# 1) 复制成独立目录（不含本仓其他东西）
cp -r benchmarks/ontology-execution ~/ontology-execution-benchmark
cd ~/ontology-execution-benchmark
rm -rf implementations/*        # 可选：不想带参考实现就删掉
git init && git add -A && git commit -m "feat: ontology-execution benchmark v0.1.0"

# 2) 在 GitHub 上建一个空仓库（不要勾选 README/LICENSE/.gitignore），然后：
git remote add origin git@github.com:<你的账号>/ontology-execution-benchmark.git
git push -u origin main
```

或者用本仓提供的打包脚本（它会额外生成一份"对外首屏"与发布检查清单）：

```bash
python scripts/publish_benchmark.py --out dist/
# 然后把 dist/ontology-execution/ 推到你的新仓库
```

## 发布后建议补的三件事

1. **加一个 `CITATION.cff`**（引用格式见 README §8）；
2. **开一个 issue 模板**：「我要提交一个实现」——把 PROTOCOL.md §1 的接口契约贴进去；
3. **在 README 首屏标注**：「参考实现由本基准作者编写，存在稻草人风险（见 PROTOCOL.md §6）；
   独立第三方实现的评测结果才是正式结论。」

## 为什么不自动建仓

本机没有 GitHub API 凭据（只有 SSH 推送权限），无法代你创建仓库；
建仓需要你的账号权限，上面两条命令一分钟就能完成。
