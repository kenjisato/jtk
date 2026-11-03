class JtkError(Exception):
    """利用者に見せてよいメッセージだけを持つ例外。個人情報の値は入れない。"""


def exit_on_error(func):
    """JtkError を stderr に出して終了コード 1 にする Typer コマンド用デコレータ。"""
    import functools

    import typer

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except JtkError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1) from None

    return wrapper
