import os
import re

# ================= 核心配置区域 =================

# 1. 输出文件名前缀 (版本号会自动加在后面)
BASE_OUTPUT_NAME = "project_code_dump"
OUTPUT_EXTENSION = ".txt"

# 2. 单个文件大小限制 (KB)
MAX_FILE_SIZE_KB = 500 

# 3. 只读取这些核心后缀 (已移除 .md)
ALLOWED_EXTENSIONS = {
    '.py', 
    '.json', '.yaml', '.yml', '.toml', '.ini',
    '.sh', '.bat', '.txt'
}

# 4. 绝对忽略的目录
IGNORED_DIRS = {
    '.git', '__pycache__', 'venv', 'env', '.venv', 
    '.pytest_cache', '.mypy_cache', 'htmlcov', 'egg-info',
    '.idea', '.vscode', 
    'node_modules', 'build', 'dist', 'wheels',
    'logs', 'log', 'data', 'dataset', 'tmp', 'temp', 'assets', 'images', 'docs', 'doc'
}

# 5. 绝对忽略的文件
IGNORED_FILES = {
    'dump_project.py', 
    '.env', 'secrets.json', 'id_rsa',
    'poetry.lock', 'package-lock.json', 'yarn.lock',
    '.DS_Store', 'Thumbs.db'
}
# =================================================

def get_next_filename_and_cleanup():
    """计算版本号并清理旧文件"""
    current_dir = os.getcwd()
    pattern = re.compile(f"^{re.escape(BASE_OUTPUT_NAME)}_(\d+){re.escape(OUTPUT_EXTENSION)}$")
    
    max_version = 0
    old_file = None

    for filename in os.listdir(current_dir):
        match = pattern.match(filename)
        if match:
            version = int(match.group(1))
            if version > max_version:
                max_version = version
                old_file = filename
    
    if old_file:
        try:
            os.remove(os.path.join(current_dir, old_file))
            print(f"🗑️  已清理旧版本: {old_file}")
        except OSError as e:
            print(f"⚠️  清理旧文件失败: {e}")

    return f"{BASE_OUTPUT_NAME}_{max_version + 1}{OUTPUT_EXTENSION}"

def is_allowed_file(filename):
    """文件过滤逻辑"""
    # 1. 特权文件：Dockerfile
    if filename == 'Dockerfile':
        return True
    
    # 2. 特权文件：README.md (忽略大小写，只留这一个 md)
    if filename.lower() == 'readme.md':
        return True
        
    # 3. 检查后缀 (白名单)
    return any(filename.endswith(ext) for ext in ALLOWED_EXTENSIONS)

def dump_directory(root_dir, output_file):
    total_files = 0
    skipped_large_files = 0
    
    IGNORED_FILES.add(output_file)
    
    try:
        with open(output_file, 'w', encoding='utf-8') as outfile:
            outfile.write(f"# Project Core Dump (Version {output_file})\n")
            outfile.write(f"# Root Directory: {root_dir}\n")
            outfile.write("="*50 + "\n\n")

            for dirpath, dirnames, filenames in os.walk(root_dir):
                dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]

                for filename in filenames:
                    if filename in IGNORED_FILES or not is_allowed_file(filename):
                        continue
                    
                    if filename.startswith(BASE_OUTPUT_NAME) and filename.endswith(OUTPUT_EXTENSION):
                        continue

                    filepath = os.path.join(dirpath, filename)
                    rel_path = os.path.relpath(filepath, root_dir)

                    try:
                        file_size_kb = os.path.getsize(filepath) / 1024
                        if file_size_kb > MAX_FILE_SIZE_KB:
                            print(f"[-] 跳过大文件 ({file_size_kb:.1f}KB): {rel_path}")
                            skipped_large_files += 1
                            continue

                        with open(filepath, 'r', encoding='utf-8') as infile:
                            content = infile.read()
                            
                        outfile.write(f"File: {rel_path}\n")
                        outfile.write("-" * 20 + "\n")
                        outfile.write(content)
                        outfile.write(f"\n\n{'='*30}\n\n")
                        
                        print(f"[+] 已打包: {rel_path}")
                        total_files += 1
                        
                    except UnicodeDecodeError:
                        print(f"[!] 跳过非文本: {rel_path}")
                    except Exception as e:
                        print(f"[!] 读取错误 {rel_path}: {e}")
                        
        print(f"\n{'-'*30}")
        print(f"✅ 打包完成！共 {total_files} 个核心文件")
        print(f"📄 新生成文件: {output_file}")
        
    except Exception as e:
        print(f"❌ 写入失败: {e}")

if __name__ == "__main__":
    current_dir = os.getcwd()
    target_filename = get_next_filename_and_cleanup()
    print(f"🚀 正在扫描核心代码 (已过滤杂乱md): {current_dir} ...")
    dump_directory(current_dir, target_filename)