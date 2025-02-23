from datetime import datetime, timedelta
import pytz  # 用于处理时区
import io
import fitz  # PyMuPDF
import json
from openai import OpenAI
# from .const import *
from pdf2image import convert_from_path
import arxiv
PAPER_SUBJECT_PROMPT="Please determine whether the paper belongs to efficient LLM inference:\n The user will input the abstract of the paper when asking the question.\n\n Requirements:\n 1. Output in JSON format: {{'relevant': boolean, 'reason': string, 'prob': float}}, where 'prob' represents the confidence level, i.e., the probability you believe the paper aligns with the proposed subject.\n 2. Relevance criteria: The paper should involve performance optimization during the inference phase of large models.\n 3. Exclusions: Training optimization, hardware design, and non-performance-related research."
ARXIV_SUBJECTS = ["artificial intelligence", "Distributed, Parallel, and Cluster Computing", "Operating Systems"]
PAPER_READ_PROMPT= "你是一个专业的计算机机器学习领域的论文阅读专家，用户会输入给你论文text内容，然后你对内容进行阅读后，将对论文的阅读结果整理成如下部分，要求用户能够在你总结中能够领略文章的宗旨和核心内容,最终返回的结果按照如下结构组织：最终结果按照如下结构进行整理{{'titile':一句话总结正篇文章特点，小红书文献阅读标题风格，或者直接翻译文章标题，前提是能让人一眼知道文章是干嘛的。'problem':主要解决的问题（约200字，核心点在于这是什么领域的问题，发现了前人工作的什么缺陷，通过什么方法解决了什么问题）, 'insights':核心观察/洞见（约200字，一定要讲清楚本文基于哪些观察，依靠哪些发现去解决问题）,'main_method':采用的主要方法（约300字，整理为python列表结构,每个主要方法作为其中一个元素，例如[xxxx,xxx,...]）,'gain':实现的收益（约100字，量化指标优先）}}"

WORK_SPACE="/home/mount_data/papers_read"

def fetch_recent_papers(subject, filter_key="inference", days=1):
    # 获取UTC当前时间
    utc_now = datetime.now(pytz.utc)
    end_date = utc_now
    start_date = end_date - timedelta(days=days)

    # 构造arXiv支持的日期范围查询字符串
    date_format = "%Y%m%d"
    query_date = (
        f"submittedDate:[{start_date.strftime(date_format)} "
        f"TO {end_date.strftime(date_format)}]"
    )

    # 组合关键词和日期查询
    full_query = f"{subject} AND {query_date}"

    # 创建搜索对象（不限制结果数量）
    search = arxiv.Search(
        query=full_query,
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending,  # 按提交日期降序
        max_results=float('inf')  # 设置为无限大以获取所有结果
    )

    return [
        result for result in search.results()
        if result.summary  # 确保摘要存在
           and filter_key in result.summary.lower()  # 不区分大小写匹配
    ]


def is_the_paper_in_subject(llm_client, summary, prompt_str):
    completion = llm_client.chat.completions.create(
        model="hunyuan-turbo",
        messages=[
            {"role": "system",
             "content": prompt_str,
             },
            {
                "role": "user",
                "content": summary,
            },
        ],
        extra_body={
            "enable_enhancement": True,  # <- 自定义参数
        },
    )
    return completion.choices[0].message.content


def get_paper_content(paper):
    # 下载PDF并解析
    response = requests.get(paper.pdf_url)
    doc = fitz.open(stream=io.BytesIO(response.content))
    text = ""
    for page in doc:
        text += page.get_text()
    return text


def sumarize_paper_content(llm_client, paper_content, system_prompt):
    completion = llm_client.chat.completions.create(
        model="hunyuan-turbo",
        messages=[
            {"role": "system",
             "content": system_prompt,
             },

            {
                "role": "user",
                "content": f"请按要求，对这篇文章进行阅读，并输出结果, 内容如下{paper_content}"
            },
        ],
        extra_body={
            "enable_enhancement": False,  # <- 自定义参数
        },
    )
    return completion.choices[0].message.content


def emoji_xhs_template(data):
    """带Emoji的模板填充"""
    template = (
            "🏗【" + data['title'] + "】\n\n"
                                   "🔥 研究痛点\n" +
            data['problem'] + "\n\n"
                              "💡 核心发现\n" +
            data['insights'] + "\n\n"
                               "🚀 技术方案\n" +
            '\n'.join([f'▪️{m}' for m in data['main_method']]) + "\n\n"
                                                                 "📊 实验结果\n" +
            data['gain'] + "\n\n"
                           "#AI技术 #可持续发展 #科研前沿"
    )
    return template


# 提取文章图片
def get_paper_images(paper_path, work_dir):
    """ 将PDF所有页面转换为图片并保存到指定目录 """
    # 转换全部页面（默认不指定first_page/last_page参数）
    images = convert_from_path(paper_path)

    saved_path = os.path.join(work_dir, 'images')
    os.makedirs(saved_path, exist_ok=True)
    for i, image in enumerate(images):
        # 生成带页码的文件名，如：first_page_1.png, first_page_2.png...
        filename = f"page_{i + 1}.png"  # 页码从1开始计数
        output_path = os.path.join(saved_path, filename)
        image.save(output_path)
    return


# 创建项目文件夹
import os
import re
import requests


def sanitize_folder_name(name):
    """
    清理文件夹名称中的非法字符
    :param name: 原始文件夹名称
    :return: 清理后的文件夹名称
    """
    # 替换非法字符为下划线
    sanitized_name = re.sub(r'[<>:"/\\|?*]', '_', name)
    # 去除首尾空格和点
    sanitized_name = sanitized_name.strip('. ')
    # 限制长度（Windows 路径最大长度为 255）
    return sanitized_name[:200]


def create_folder_from_arxiv(work_space, title, arxiv_id):
    """
    根据 arXiv ID 创建文件夹
    :param arxiv_id: arXiv 文章的 ID（如 "2103.00001"）
    """
    try:

        # 清理标题作为文件夹名
        folder_name = sanitize_folder_name(title)

        # 尝试创建文件夹
        os.makedirs(os.path.join(work_space, folder_name), exist_ok=True)
        print(f"文件夹创建成功: {folder_name}")

    except Exception as e:
        print(f"使用论文标题创建文件夹失败: {str(e)}")
        print(f"回退到使用 arXiv ID 创建文件夹...")

        # 使用 arXiv ID 创建文件夹
        folder_name = f"arxiv_{arxiv_id}"
        os.makedirs(os.path.join(work_space, folder_name), exist_ok=True)
        print(f"文件夹创建成功: {folder_name}")
    return os.path.join(work_space, folder_name)


def main_work_flow(arxiv_subject=ARXIV_SUBJECTS, paper_subject_prompt=PAPER_SUBJECT_PROMPT,
                   paper_read_prompt=PAPER_READ_PROMPT,
                   filter_key="inference", days=3):
    # 初步按照filter查找所有符合要求的论文
    client = OpenAI(
        api_key="your api key",  # 混元 APIKey
        base_url="https://api.hunyuan.cloud.tencent.com/v1",  # 混元 endpoint
    )
    # 初筛papers
    paper_list = []
    for i in arxiv_subject:
        tmp_res = fetch_recent_papers(i, filter_key, days)
        paper_list.extend(tmp_res)
    # LLM辅助筛选papers
    print("初筛papers完成")
    papers_selected = []
    for paper_meta_info in paper_list:
        paper_select_info = is_the_paper_in_subject(client, paper_meta_info.summary, paper_subject_prompt)
        paper_select_info = json.loads(paper_select_info)
        paper_relevant = paper_select_info['relevant']
        paper_prob = paper_select_info['prob']
        print(paper_select_info)

        if paper_relevant and paper_prob >= 0.9:
            papers_selected.append(paper_meta_info)
    # 针对每篇paper进行信息抽取与大模型阅读。
    # 将每篇文章仍然进行下载并提取图片，并将刚才的阅读结果整理成txt
    print("复筛papers完成")
    for paper in papers_selected:
        paper_name = paper.title
        paper_id = paper.pdf_url.split('/')[-1]
        work_dir_path = create_folder_from_arxiv(WORK_SPACE, paper_name, paper_id)
        # 下载文件
        paper_path = paper.download_pdf(work_dir_path)
        source_path = paper.download_source(work_dir_path)
        # 提取图片
        get_paper_images(paper_path, work_dir_path)
        paper_context = get_paper_content(paper)
        llm_summarized = sumarize_paper_content(client, paper_context, paper_read_prompt)
        llm_summarized = json.loads(llm_summarized)
        llm_summ_xhs_style = emoji_xhs_template(llm_summarized)
        with open(os.path.join(work_dir_path, "summarized"), 'w', encoding='utf-8') as f:
            f.write(llm_summ_xhs_style)
    return


if __name__ == '__main__':
    main_work_flow()




